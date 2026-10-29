from uuid import uuid4

from fastapi import Body, Depends
from pydantic import Field

from ..models import Decision, Strict
from ..service import DomainError, required
from ..store import now
from .capability_contracts import TEMPLATES, CapabilitySpec, input_schema, output_schema
from .capability_engine import dependencies, execute, invoke
from .ontology_contracts import RollbackRequest
from .ontology_engine import digest


class PreviewRequest(Strict):
    arguments: dict = Field(default_factory=dict)


def install(app, store, auth, permit):
    def definition(cap):
        return {
            "name": cap["spec"]["name"],
            "title": cap["spec"]["title"],
            "description": cap["spec"]["description"],
            "inputSchema": cap["input_schema"],
            "outputSchema": cap["output_schema"],
            "version": cap["version"],
            "annotations": {
                "readOnlyHint": cap["spec"]["template"] != "schedule_draft",
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }

    def catalog(tx, p):
        result = []
        for record in tx.all("v1_capability"):
            if record.get("active_version"):
                cap = required(
                    tx, "v1_capability_version", record["name"] + ":" + str(record["active_version"])
                )
                if p["role"] in cap["spec"]["roles"]:
                    result.append(definition(cap))
        return result

    @app.get("/api/v1/capability-templates")
    def templates(p=Depends(auth)):
        permit(p, "operator", "approver")
        return [
            {"id": key, "description": value, "dsl_schema": CapabilitySpec.model_json_schema()}
            for key, value in TEMPLATES.items()
        ]

    @app.get("/api/v1/capabilities")
    def capabilities(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            if p["role"] == "viewer" or p.get("data_policy") is not None:
                return catalog(tx, p)
            return tx.all("v1_capability")

    @app.post("/api/v1/capabilities", status_code=201)
    def create(body: CapabilitySpec, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            spec = body.model_dump()
            dependencies(tx, spec)
            current = tx.get("v1_capability", body.name)
            revision = (current or {}).get("revision", 0) + 1
            result = {
                "name": body.name,
                "spec": spec,
                "revision": revision,
                "edited_by": p["user"],
                "created_at": (current or {}).get("created_at", now()),
                "updated_at": now(),
                "active_version": (current or {}).get("active_version"),
                "generated_id": None,
            }
            tx.put("v1_capability", body.name, result)
            tx.audit(p["user"], "capability.draft.saved", body.name, {"revision": revision})
            return result

    @app.post("/api/v1/capabilities/{name}/generate")
    def generate(name: str, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            record = required(tx, "v1_capability", name)
            version, snapshot = dependencies(tx, record["spec"])
            number = (
                max(
                    [v["version"] for v in tx.all("v1_capability_version") if v["spec"]["name"] == name]
                    or [0]
                )
                + 1
            )
            key = name + ":" + str(number)
            example = {}
            if record["spec"]["template"] == "list_due_orders":
                example = {"days": 7}
            else:
                obj = next(
                    o for o in version["spec"]["objects"] if o["name"] == record["spec"]["root_object"]
                )
                entity = next((e for e in snapshot["entities"] if e["entity_type"] == obj["name"]), None)
                if not entity:
                    raise DomainError("已发布本体没有可生成沙箱用例的根对象", 409)
                example["entity_id" if record["spec"]["template"] == "entity_lookup" else "order_id"] = str(
                    entity["data"][obj["identity"]]
                )
                if record["spec"]["template"] == "schedule_draft":
                    example.update(
                        extra_hours=2, note="自动生成的沙箱排产用例", idempotency_key="sandbox-" + uuid4().hex
                    )
            result = {
                "id": key,
                "version": number,
                "revision": record["revision"],
                "spec": record["spec"],
                "status": "generated",
                "generated_by": p["user"],
                "generated_at": now(),
                "example": example,
                "ontology_digest": version["digest"],
                "input_schema": input_schema(record["spec"]),
                "output_schema": output_schema(record["spec"]),
            }
            result["digest"] = digest(result["spec"])
            tx.put("v1_capability_version", key, result)
            record["generated_id"] = key
            tx.put("v1_capability", name, record)
            tx.audit(p["user"], "capability.generated", key)
            return {**result, "tool": definition(result), "rest_path": "/api/v1/invoke/" + name}

    @app.get("/api/v1/capabilities/{name}/versions")
    def versions(name: str, p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            required(tx, "v1_capability", name)
            return [v for v in tx.all("v1_capability_version") if v["spec"]["name"] == name]

    @app.post("/api/v1/capabilities/{name}/preview")
    def preview(name: str, body: PreviewRequest, p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            record = required(tx, "v1_capability", name)
            cap = required(tx, "v1_capability_version", record["generated_id"] or "")
            result = execute(tx, cap, body.arguments, p, sandbox=True)
            tx.audit(p["user"], "capability.previewed", cap["id"])
            return result

    @app.post("/api/v1/capabilities/{name}/test")
    def test(name: str, p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            record = required(tx, "v1_capability", name)
            cap = required(tx, "v1_capability_version", record["generated_id"] or "")
            version, snapshot = dependencies(tx, cap["spec"])
            checks = []

            def check(label, args, role, expected=None):
                try:
                    value = execute(tx, cap, args, {**p, "role": role}, sandbox=True)
                    checks.append({"name": label, "passed": expected is None, "status": 200})
                    return value
                except DomainError as exc:
                    checks.append({"name": label, "passed": exc.status == expected, "status": exc.status})
                    return None

            role = cap["spec"]["roles"][0]
            before = len(tx.all("v1_action"))
            result = check("generated_example", cap["example"], role)
            check("reject_unknown_input", {**cap["example"], "arbitrary_sql": "SELECT secret"}, role, 422)
            check(
                "reject_wrong_parameter_types",
                {k: [] for k in input_schema(cap["spec"])["properties"]},
                role,
                422,
            )
            for denied_role in {"viewer", "operator", "approver", "admin"} - set(cap["spec"]["roles"]):
                check("deny_role_" + denied_role, cap["example"], denied_role, 403)
            # Assert sandbox cannot create or approve an action.
            check("repeat_without_side_effects", cap["example"], role)
            checks.append({"name": "sandbox_no_actions", "passed": before == len(tx.all("v1_action"))})
            report = {
                "id": uuid4().hex,
                "capability_version": cap["id"],
                "at": now(),
                "actor": p["user"],
                "passed": all(c["passed"] for c in checks),
                "checks": checks,
                "preview": result,
                "digest": cap["digest"],
                "ontology_digest": version["digest"],
                "snapshot_id": snapshot["id"],
            }
            tx.put("v1_capability_test", report["id"], report)
            cap["test_id"] = report["id"]
            tx.put("v1_capability_version", cap["id"], cap)
            tx.audit(p["user"], "capability.tested", cap["id"], {"passed": report["passed"]})
            return report

    @app.post("/api/v1/capabilities/{name}/publish")
    def publish(name: str, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            record = required(tx, "v1_capability", name)
            cap = required(tx, "v1_capability_version", record["generated_id"] or "")
            if cap["generated_by"] == p["user"] or record["edited_by"] == p["user"]:
                raise DomainError("能力创建者不能批准自己的发布", 403)
            if cap["revision"] != record["revision"] or cap["digest"] != digest(record["spec"]):
                raise DomainError("草案已变化，请重新生成并测试", 409)
            version, snapshot = dependencies(tx, cap["spec"])
            test = tx.get("v1_capability_test", cap.get("test_id", ""))
            if (
                not test
                or not test["passed"]
                or test["digest"] != cap["digest"]
                or test["ontology_digest"] != version["digest"]
                or test["snapshot_id"] != snapshot["id"]
            ):
                raise DomainError("缺少当前本体快照的通过测试，请重新执行沙箱测试", 409)
            cap.update(status="published", published_by=p["user"], published_at=now())
            tx.put("v1_capability_version", cap["id"], cap)
            record["active_version"] = cap["version"]
            tx.put("v1_capability", name, record)
            tx.audit(p["user"], "capability.published", cap["id"])
            return {**definition(cap), "rest_path": "/api/v1/invoke/" + name}

    @app.post("/api/v1/capabilities/{name}/rollback")
    def rollback(name: str, body: RollbackRequest, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            record = required(tx, "v1_capability", name)
            cap = required(tx, "v1_capability_version", name + ":" + str(body.version))
            if cap["status"] != "published":
                raise DomainError("只能回滚到已发布能力版本", 409)
            dependencies(tx, cap["spec"])
            record["active_version"] = body.version
            tx.put("v1_capability", name, record)
            tx.audit(p["user"], "capability.rolled_back", name, body.model_dump())
            return definition(cap)

    @app.get("/api/v1/tools")
    def tools(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            return {"tools": catalog(tx, p)}

    @app.get("/api/v1/generated/openapi.json")
    def openapi(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            items = catalog(tx, p)
        paths = {}
        for item in items:
            paths["/api/v1/invoke/" + item["name"]] = {
                "post": {
                    "operationId": item["name"],
                    "summary": item["title"],
                    "description": item["description"],
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": item["inputSchema"]}},
                    },
                    "responses": {
                        "200": {
                            "description": "Verified ontology result",
                            "content": {"application/json": {"schema": item["outputSchema"]}},
                        },
                        "403": {"description": "Permission denied"},
                        "503": {"description": "Source unavailable or stale"},
                    },
                }
            }
        return {
            "openapi": "3.1.0",
            "info": {"title": "OntoBridge published capabilities", "version": "1.0"},
            "paths": paths,
            "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
        }

    @app.post("/api/v1/invoke/{name}")
    def call(name: str, arguments: dict = Body(...), p=Depends(auth)):
        return invoke(store, p, name, arguments)

    @app.post("/api/v1/mcp/invoke/{name}")
    def mcp_call(name: str, arguments: dict = Body(...), p=Depends(auth)):
        return invoke(store, p, name, arguments, transport="mcp")

    @app.get("/api/v1/capability-operations")
    def operations(p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            calls = sorted(tx.all("v1_call"), key=lambda c: c["at"], reverse=True)[:1000]
            return {
                "calls": calls,
                "count": len(calls),
                "errors": sum(c["status"] == "failed" for c in calls),
                "average_ms": round(sum(c["duration_ms"] for c in calls) / len(calls), 2) if calls else 0,
            }

    @app.get("/api/v1/actions")
    def actions(p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            return [
                a
                for a in tx.all("v1_action")
                if p["role"] in ("approver", "admin") or a["created_by"] == p["user"]
            ]

    @app.post("/api/v1/actions/{action_id}/decision")
    def decide(action_id: str, body: Decision, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            action = required(tx, "v1_action", action_id)
            if action["created_by"] == p["user"]:
                raise DomainError("不能审批自己的动作草案", 403)
            if action["result"]["status"] != "pending_approval":
                raise DomainError("草案已审批", 409)
            action["result"].update(
                status="approved_draft" if body.decision == "approve" else "rejected",
                executed=False,
                decision_by=p["user"],
                decision_at=now(),
                reason=body.reason,
            )
            tx.put("v1_action", action_id, action)
            tx.audit(p["user"], "action.draft." + body.decision, action_id)
            return action
