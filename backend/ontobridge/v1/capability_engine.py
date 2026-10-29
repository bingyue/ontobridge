"""One governed executor used by generated REST, sandbox and MCP gateways."""

import time
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from jsonschema import Draft202012Validator

from ..models import ENTITIES, RELATIONS
from ..service import Business, DomainError, required
from ..store import business_today, now
from .ontology_engine import digest


class PublishedBusiness(Business):
    def __init__(self, snapshot, definitions):
        class Snapshot:
            def all(self, kind):
                if kind == "object":
                    return [{**o, "entity": o["entity_type"]} for o in snapshot["entities"]]
                return []

        super().__init__(Snapshot())
        relation_fields = {r["name"]: r for r in definitions["relations"]}
        self.links = {}
        for relation in snapshot["relationships"]:
            rule = relation_fields[relation["relation_type"]]
            if rule["cardinality"] in ("many_to_one", "one_to_one"):
                self.links[(relation["from_canonical_id"], rule["from_field"], rule["to_object"])] = relation[
                    "to_canonical_id"
                ]

    def relation(self, obj, field, entity):
        target = self.links.get((obj["id"], field, entity)) if obj else None
        return self.by_id.get(target)

    def warnings(self):
        return []  # Freshness and quality are enforced before any template executes.


def dependencies(tx, spec):
    version = required(tx, "v1_ontology_version", spec["ontology_id"] + ":" + str(spec["ontology_version"]))
    if version["status"] != "published":
        raise DomainError("能力只能消费已发布本体", 409)
    objects = {o["name"]: o for o in version["spec"]["objects"]}
    if spec["root_object"] not in objects:
        raise DomainError("本体缺少能力根对象", 409)
    if spec["template"] != "entity_lookup":
        necessary = ["SalesOrder"] if spec["template"] == "list_due_orders" else ["SalesOrder", "WorkOrder"]
        if spec["template"] in ("delivery_risk", "material_shortage", "schedule_draft") or (
            spec["template"] == "order_360" and set(ENTITIES) <= set(objects)
        ):
            necessary = list(ENTITIES)
        for name in necessary:
            if name not in objects:
                raise DomainError("模板依赖的对象缺失：" + name, 409)
            expected = ENTITIES[name].model_json_schema()["properties"]
            attrs = {a["name"]: a for a in objects[name]["attributes"]}
            if set(expected) - set(attrs):
                raise DomainError("模板依赖的字段缺失：" + name, 409)
            if objects[name]["identity"] != "id" or any(
                attrs[field]["type"] != definition.get("format", definition["type"])
                for field, definition in expected.items()
            ):
                raise DomainError("模板依赖的业务身份或字段类型不匹配：" + name, 409)
        for source, field, target, *_ in RELATIONS:
            if source not in necessary or target not in necessary:
                continue
            rules = [
                r
                for r in version["spec"]["relations"]
                if (r["from_object"], r["from_field"], r["to_object"]) == (source, field, target)
            ]
            if (
                len(rules) != 1
                or rules[0]["to_field"] != "id"
                or rules[0]["cardinality"] not in ("many_to_one", "one_to_one")
                or not rules[0]["required"]
            ):
                raise DomainError("模板需要唯一且必填的已发布业务关系：" + source + "." + field, 409)
    snapshot = required(tx, "v1_snapshot", version["snapshot_id"])
    if not snapshot["valid"] or version.get("refresh_valid") is False:
        raise DomainError("本体最近数据质量校验失败，能力调用已阻止", 409)
    # Pydantic business models check cross-field invariants too, not merely field existence.
    if spec["template"] != "entity_lookup":
        try:
            for entity in snapshot["entities"]:
                if entity["entity_type"] in ENTITIES:
                    model = ENTITIES[entity["entity_type"]]
                    model.model_validate({k: entity["data"].get(k) for k in model.model_fields})
        except ValueError as exc:
            raise DomainError("已发布对象不满足制造业模板的业务约束", 409) from exc
    return version, snapshot


def visible(entity, principal):
    policy = principal.get("data_policy")
    if policy is None:
        return True
    rule = policy.get("objects", {}).get(entity["entity_type"])
    if not rule:
        return False
    return all(entity["data"].get(field) in values for field, values in rule.get("rows", {}).items())


def project(entity, principal):
    policy = principal.get("data_policy")
    if policy is None:
        return entity
    rule = policy.get("objects", {}).get(entity["entity_type"], {})
    fields = rule.get("columns", [])
    data = entity["data"] if fields == "*" else {k: v for k, v in entity["data"].items() if k in fields}
    # Provenance may expose source primary keys; require explicit grant for restricted principals.
    return {
        "id": entity["id"],
        "entity_type": entity["entity_type"],
        "data": data,
        "provenance": entity["provenance"] if rule.get("provenance", False) else [],
    }


def field_visible(principal, object_name, field):
    policy = principal.get("data_policy")
    if policy is None:
        return True
    columns = policy.get("objects", {}).get(object_name, {}).get("columns", [])
    return columns == "*" or field in columns


def relation_visible(principal, rule):
    fields = [(rule["from_object"], rule["from_field"]), (rule["to_object"], rule["to_field"])]
    if rule.get("via_object"):
        fields.extend((rule["via_object"], field) for field in [rule["via_from_field"], rule["via_to_field"]])
    return all(field_visible(principal, obj, field) for obj, field in fields)


def freshness(tx, snapshot):
    sources = {s["id"]: s for s in tx.all("v1_source")}
    pipelines = {p["id"]: p for p in tx.all("v1_pipeline")}
    for entity in snapshot["entities"]:
        for ref in entity["provenance"]:
            source, pipeline = sources.get(ref["source_id"]), pipelines.get(ref["pipeline_id"])
            if not source or not pipeline or pipeline.get("last_error"):
                raise DomainError("依赖来源缺失或同步失败，暂停返回业务结论", 503)
            age = (datetime.now(UTC) - datetime.fromisoformat(ref["source_updated_at"])).total_seconds()
            if age > source["freshness_minutes"] * 60:
                raise DomainError("已发布快照超过来源时效 SLA，请同步来源并刷新本体", 503)


def execute(tx, cap, arguments, principal, sandbox=False):
    started = time.monotonic()
    spec = cap["spec"]
    if principal["role"] not in spec["roles"]:
        raise DomainError("当前角色未获得该能力授权", 403)
    if list(Draft202012Validator(cap["input_schema"]).iter_errors(arguments)):
        raise DomainError("能力参数不符合已发布 JSON Schema", 422)
    version, snapshot = dependencies(tx, spec)
    freshness(tx, snapshot)
    entities = snapshot["entities"]
    allowed = [e for e in entities if visible(e, principal)]
    root_def = next(o for o in version["spec"]["objects"] if o["name"] == spec["root_object"])
    template = spec["template"]
    root = None
    if template != "list_due_orders":
        if not field_visible(principal, spec["root_object"], root_def["identity"]):
            raise DomainError("当前凭据无权按该业务身份查询", 403)
        key = arguments["entity_id" if template == "entity_lookup" else "order_id"]
        root = next(
            (
                e
                for e in allowed
                if e["entity_type"] == spec["root_object"] and str(e["data"][root_def["identity"]]) == key
            ),
            None,
        )
        if not root:
            raise DomainError("业务对象不存在或无权访问", 404)
    if template != "entity_lookup" and principal.get("data_policy") is not None:
        # Full manufacturing facts affect shared queues/stock. Do not report falsely complete totals
        # or leak hidden rows through aggregates. A scoped lookup remains available.
        if len(allowed) != len(entities) or any(
            (
                project(e, principal)["data"] != e["data"]
                or project(e, principal)["provenance"] != e["provenance"]
            )
            for e in entities
        ):
            raise DomainError("该模板需要完整的业务计算范围，当前行列权限不足", 403)
    used = entities
    if template == "entity_lookup":
        allowed_ids = {e["id"] for e in allowed}
        allowed_assets = {ref["asset_id"] for e in allowed for ref in e["provenance"]}
        allowed_relations = {
            r["name"] for r in version["spec"]["relations"] if relation_visible(principal, r)
        }
        links = [
            r
            for r in snapshot["relationships"]
            if root["id"] in (r["from_canonical_id"], r["to_canonical_id"])
            and r["from_canonical_id"] in allowed_ids
            and r["to_canonical_id"] in allowed_ids
            and r["relation_type"] in allowed_relations
            and all(ref["asset_id"] in allowed_assets for ref in r["evidence_ref"])
        ]
        selected_ids = {root["id"]}
        for link in links[: spec["max_results"]]:
            selected_ids.update([link["from_canonical_id"], link["to_canonical_id"]])
        used = [project(e, principal) for e in allowed if e["id"] in selected_ids]
        result = {
            "object": project(root, principal),
            "related": [e for e in used if e["id"] != root["id"]],
            "relations": [
                {k: r[k] for k in ["from_canonical_id", "to_canonical_id", "relation_type"]}
                for r in links[: spec["max_results"]]
            ],
            "truncated": len(links) > spec["max_results"],
        }
    else:
        business = PublishedBusiness(snapshot, version["spec"])
        order = business.by_id.get(root["id"]) if root else None
        if template == "production_progress":
            result = business.progress(order)
        elif template == "material_shortage":
            result = business.materials(order)
        elif template == "delivery_risk":
            result = business.risk(order)
        elif template == "order_360":
            complete = set(ENTITIES) <= {o["name"] for o in version["spec"]["objects"]}
            result = {
                "order": order,
                "progress": business.progress(order),
                "risk": business.risk(order)
                if complete
                else {"level": "unknown", "reasons": ["已发布本体缺少产能或物料对象，无法计算完整风险"]},
                "relationships": [
                    r
                    for r in snapshot["relationships"]
                    if root["id"] in (r["from_canonical_id"], r["to_canonical_id"])
                ],
            }
        elif template == "list_due_orders":
            today = business_today()
            due = today + timedelta(days=arguments.get("days", 7))
            orders = [
                o
                for o in business.rows("SalesOrder")
                if today <= date.fromisoformat(o["data"]["due_date"]) <= due
            ]
            result = {
                "orders": orders[: spec["max_results"]],
                "count": len(orders),
                "truncated": len(orders) > spec["max_results"],
            }
        else:
            proposed = {
                "order_id": arguments["order_id"],
                "extra_hours": arguments["extra_hours"],
                "note": arguments["note"],
            }
            result = {
                "status": "sandbox_preview" if sandbox else "pending_approval",
                "proposal": proposed,
                "before": business.risk(order),
                "after": business.risk(order, arguments["extra_hours"]),
                "writeback_enabled": False,
                "executed": False,
            }
            if not sandbox:
                action_key = digest([principal["user"], cap["id"], arguments["idempotency_key"]])
                existing = tx.get("v1_action", action_key)
                if existing:
                    if existing["request_digest"] != digest(arguments):
                        raise DomainError("幂等键已用于不同的排产草案", 409)
                    result = existing["result"]
                else:
                    result["action_id"] = action_key
                    tx.put(
                        "v1_action",
                        action_key,
                        {
                            "id": action_key,
                            "created_by": principal["user"],
                            "created_at": now(),
                            "capability_version": cap["id"],
                            "request_digest": digest(arguments),
                            "result": result,
                        },
                    )
                    tx.audit(principal["user"], "action.draft.created", action_key)
    if time.monotonic() - started > spec["timeout_seconds"]:
        raise DomainError("能力执行超过配置的时间预算", 504)
    envelope = {
        "result": result,
        "meta": {
            "computed_at": now(),
            "ontology_version": version["id"],
            "capability_version": cap["id"],
            "snapshot_id": snapshot["id"],
            "rule_version": spec["rule_version"],
            "provenance": [p for entity in used for p in entity["provenance"]],
            "sandbox": sandbox,
        },
    }
    Draft202012Validator(cap["output_schema"]).validate(envelope)
    return envelope


def invoke(store, principal, name, arguments, transport="rest"):
    started = time.monotonic()
    error, result, cap_id = None, None, name
    try:
        with store.transaction(principal["tenant"]) as tx:
            record = tx.get("v1_capability", name)
            if not record or not record.get("active_version"):
                raise DomainError("能力不存在或尚未发布", 404)
            cap = required(tx, "v1_capability_version", name + ":" + str(record["active_version"]))
            cap_id = cap["id"]
            result = execute(tx, cap, arguments, principal)
    except DomainError as exc:
        error = exc
    except Exception:
        error = DomainError("能力内部执行失败，未返回未验证结果", 500)
    with store.transaction(principal["tenant"]) as tx:
        key = uuid4().hex
        log = {
            "id": key,
            "at": now(),
            "actor": principal["user"],
            "capability": name,
            "version": cap_id,
            "transport": transport,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "status": "failed" if error else "succeeded",
            "error_status": error.status if error else None,
            "error": error.message if error else None,
        }
        tx.put("v1_call", key, log)
        tx.audit(
            principal["user"],
            "capability.call." + log["status"],
            cap_id,
            {"call_id": key, "transport": transport},
        )
    if error:
        raise error
    return result
