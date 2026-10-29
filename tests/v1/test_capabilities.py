from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from ontobridge.api import create_app
from ontobridge.demo import seed
from ontobridge.service import Business
from ontobridge.v1.pipelines import GatewayWorker

TOKENS = {
    r: r + "-capability-test-token-long-enough" for r in ["operator", "approver", "viewer", "scoped", "admin"]
}


def api(client, method, path, body=None, role="operator", code=200):
    response = client.request(
        method, "/api/v1" + path, json=body, headers={"Authorization": "Bearer " + TOKENS[role]}
    )
    assert response.status_code == code, response.text
    return response.json()


@pytest.fixture
def platform(tmp_path):
    credentials = {
        token: {"user": role, "tenant": "demo", "role": role if role != "scoped" else "viewer"}
        for role, token in TOKENS.items()
    }
    credentials[TOKENS["scoped"]]["data_policy"] = {
        "objects": {
            "SalesOrder": {
                "rows": {"customer": ["山川户外"]},
                "columns": ["id", "customer"],
                "provenance": False,
            }
        }
    }
    credentials["other-factory-token-long-enough"] = {"user": "other", "tenant": "other", "role": "operator"}
    app = create_app(tmp_path, demo=False, credentials=credentials)
    seed(app.state.store, app.state.source_root)
    with TestClient(app) as client:

        def post(path, body=None, **kw):
            return api(client, "POST", path, body, **kw)

        template = api(client, "GET", "/ontology-template")
        ontology = post("/ontologies", template, code=201)
        with app.state.store.transaction("demo") as tx:
            sources, mappings = tx.all("source"), tx.all("mapping")
        source_ids = {}
        for source in sources:
            value = post(
                "/sources",
                {
                    "name": source["name"],
                    "kind": source["kind"],
                    "system": source["system"],
                    "path": source["path"].removeprefix("demo/"),
                },
                code=201,
            )
            post("/sources/" + value["id"] + "/discover")
            source_ids[source["id"]] = value["id"]
        worker = GatewayWorker(app.state.store, app.state.source_root, app.state.connector_registry)
        for mapping in mappings:
            p = post(
                "/pipelines",
                {
                    "name": mapping["entity"],
                    "source_id": source_ids[mapping["source_id"]],
                    "table": mapping["table"],
                    "primary_key": mapping["fields"]["id"],
                },
                code=201,
            )
            post("/pipelines/" + p["id"] + "/runs", code=202)
            worker.tick("demo")
            attrs = next(o for o in template["objects"] if o["name"] == mapping["entity"])["attributes"]
            fields = {
                a["name"]: {"source": mapping["fields"][a["name"]], "transform": a["type"]} for a in attrs
            }
            m = post(
                "/mappings",
                {
                    "ontology_id": ontology["id"],
                    "object": mapping["entity"],
                    "pipeline_id": p["id"],
                    "fields": fields,
                    "explanation": "与 mock ERP/MES fixture 原始字段逐项对照",
                },
                code=201,
            )
            post("/mappings/" + m["id"] + "/approve", role="approver")
            template["mapping_ids"].append(m["id"])
        post("/ontologies/" + ontology["id"] + "/draft", {"revision": 1, "spec": template})
        published = post("/ontologies/" + ontology["id"] + "/publish", {"revision": 2}, role="approver")
        yield app, client, ontology, published


def define(client, ontology, template="order_360", name="get_order_360", roles=None):
    body = {
        "name": name,
        "title": "订单全景",
        "description": "通过已发布本体查询订单状态和来源",
        "ontology_id": ontology["id"],
        "ontology_version": 1,
        "template": template,
    }
    if roles is not None:
        body["roles"] = roles
    return api(client, "POST", "/capabilities", body, code=201)


def publish(client, name):
    api(client, "POST", f"/capabilities/{name}/generate")
    report = api(client, "POST", f"/capabilities/{name}/test")
    assert report["passed"], report
    return api(client, "POST", f"/capabilities/{name}/publish", role="approver")


def test_generate_test_publish_and_source_reconciliation(platform):
    app, client, ontology, version = platform
    define(client, ontology)
    generated = api(client, "POST", "/capabilities/get_order_360/generate")
    api(client, "POST", "/capabilities/get_order_360/publish", role="approver", code=409)
    api(client, "POST", "/invoke/get_order_360", {"order_id": "SO-2026-0088"}, code=404)
    report = api(client, "POST", "/capabilities/get_order_360/test")
    assert report["passed"] and all(c["passed"] for c in report["checks"])
    api(client, "POST", "/capabilities/get_order_360/publish", role="approver")
    result = api(client, "POST", "/invoke/get_order_360", {"order_id": "SO-2026-0088"}, role="viewer")
    assert result["meta"]["ontology_version"] == version["id"]
    assert result["meta"]["capability_version"] == generated["id"]
    assert len(result["result"]["progress"]["work_orders"]) == 2
    assert result["result"]["progress"]["quantity"] == 1200
    with app.state.store.transaction("demo") as tx:
        business = Business(tx)
        assert result["result"]["risk"] == business.risk(business.order("SO-2026-0088"))
    schema = api(client, "GET", "/generated/openapi.json", role="viewer")
    assert "/api/v1/invoke/get_order_360" in schema["paths"]
    tools = api(client, "GET", "/tools", role="viewer")["tools"]
    assert tools[0]["name"] == "get_order_360" and tools[0]["annotations"]["readOnlyHint"]
    api(client, "POST", "/invoke/get_order_360", {"order_id": "SO-2026-0088", "sql": "bad"}, code=422)
    ops = api(client, "GET", "/capability-operations")
    assert ops["errors"] == 2 and ops["count"] == 3
    assert all(c["duration_ms"] >= 0 for c in ops["calls"])


def test_roles_row_column_policy_and_no_aggregate_leak(platform):
    _, client, ontology, _ = platform
    define(client, ontology, "entity_lookup", "read_order", ["operator", "viewer"])
    publish(client, "read_order")
    result = api(client, "POST", "/invoke/read_order", {"entity_id": "SO-2026-0088"}, role="scoped")
    assert set(result["result"]["object"]["data"]) == {"id", "customer"}
    assert result["meta"]["provenance"] == [] and result["result"]["related"] == []
    api(client, "POST", "/invoke/read_order", {"entity_id": "SO-2026-0089"}, role="scoped", code=404)
    api(client, "POST", "/invoke/read_order", {"entity_id": "SO-2026-0088"}, role="approver", code=403)
    assert api(client, "GET", "/tools", role="approver")["tools"] == []
    define(client, ontology)
    publish(client, "get_order_360")
    api(client, "POST", "/invoke/get_order_360", {"order_id": "SO-2026-0088"}, role="scoped", code=403)


def test_stale_disconnect_quality_fail_closed_and_audited(platform):
    app, client, ontology, version = platform
    define(client, ontology)
    publish(client, "get_order_360")
    body = {"order_id": "SO-2026-0088"}
    with app.state.store.transaction("demo") as tx:
        snapshot = tx.get("v1_snapshot", version["snapshot_id"])
        original = deepcopy(snapshot)
        snapshot["entities"][0]["provenance"][0]["source_updated_at"] = "2000-01-01T00:00:00+00:00"
        tx.put("v1_snapshot", snapshot["id"], snapshot)
    api(client, "POST", "/invoke/get_order_360", body, code=503)
    with app.state.store.transaction("demo") as tx:
        tx.put("v1_snapshot", original["id"], original)
        p = tx.all("v1_pipeline")[0]
        p["last_error"] = "disconnect"
        tx.put("v1_pipeline", p["id"], p)
    api(client, "POST", "/invoke/get_order_360", body, code=503)
    with app.state.store.transaction("demo") as tx:
        p["last_error"] = None
        tx.put("v1_pipeline", p["id"], p)
        v = tx.get("v1_ontology_version", version["id"])
        v["refresh_valid"] = False
        tx.put("v1_ontology_version", v["id"], v)
    api(client, "POST", "/invoke/get_order_360", body, code=409)
    assert api(client, "GET", "/capability-operations")["errors"] == 3


def test_published_ontology_required_test_invalidated_and_capability_rollback(platform):
    _, client, ontology, version = platform
    draft = api(client, "POST", "/ontologies", api(client, "GET", "/ontology-template"), code=201)
    api(
        client,
        "POST",
        "/capabilities",
        {
            "name": "bad",
            "title": "bad",
            "description": "cannot consume draft",
            "ontology_id": draft["id"],
            "ontology_version": 1,
            "template": "entity_lookup",
        },
        code=404,
    )
    define(client, ontology)
    publish(client, "get_order_360")
    define(client, ontology)
    generated = api(client, "POST", "/capabilities/get_order_360/generate")
    assert generated["version"] == 2
    api(client, "POST", "/capabilities/get_order_360/test")
    api(client, "POST", "/ontologies/" + ontology["id"] + "/refresh")
    api(client, "POST", "/capabilities/get_order_360/publish", role="approver", code=409)
    api(client, "POST", "/capabilities/get_order_360/test")
    api(client, "POST", "/capabilities/get_order_360/publish", role="approver")
    api(
        client,
        "POST",
        "/capabilities/get_order_360/rollback",
        {"version": 1, "reason": "恢复已验证工具版本"},
        role="approver",
    )
    result = api(client, "POST", "/invoke/get_order_360", {"order_id": "SO-2026-0088"})
    assert result["meta"]["capability_version"] == "get_order_360:1"


def test_all_manufacturing_templates_and_idempotent_draft(platform):
    app, client, ontology, _ = platform
    for template in [
        "delivery_risk",
        "material_shortage",
        "production_progress",
        "list_due_orders",
        "schedule_draft",
    ]:
        roles = ["operator", "approver"] if template == "schedule_draft" else None
        define(client, ontology, template, template, roles)
        publish(client, template)
        args = {"days": 14} if template == "list_due_orders" else {"order_id": "SO-2026-0088"}
        if template == "schedule_draft":
            args.update(extra_hours=2, note="人工核对后请求排产建议", idempotency_key="unique-draft-001")
        result = api(client, "POST", "/invoke/" + template, args)
        assert result["meta"]["provenance"]
        if template == "schedule_draft":
            again = api(client, "POST", "/invoke/" + template, args)
            assert again["result"]["action_id"] == result["result"]["action_id"]
            api(client, "POST", "/invoke/" + template, {**args, "extra_hours": 3}, code=409)
            action = api(
                client,
                "POST",
                "/actions/" + result["result"]["action_id"] + "/decision",
                {"decision": "approve", "reason": "已确认这是草案，不提交 MES"},
                role="approver",
            )
            assert action["result"]["status"] == "approved_draft" and action["result"]["executed"] is False
    with app.state.store.transaction("demo") as tx:
        assert len(tx.all("v1_action")) == 1


def test_all_entry_points_keep_tenant_and_policy_boundaries(platform):
    _, client, ontology, _ = platform
    define(client, ontology, "entity_lookup", "read_order")
    publish(client, "read_order")
    for path in [
        "/sources",
        "/pipelines",
        "/assets",
        "/ontologies",
        "/mappings",
        "/operations",
        "/capability-operations",
        "/actions",
    ]:
        api(client, "GET", path, role="scoped", code=403)
    for path in ["/api/overview", "/api/ontology", "/api/sources"]:
        assert client.get(path, headers={"Authorization": "Bearer " + TOKENS["scoped"]}).status_code == 403
    assert (
        client.post(
            "/api/capabilities/get_order_360/call",
            json={"order_id": "SO-2026-0088"},
            headers={"Authorization": "Bearer " + TOKENS["scoped"]},
        ).status_code
        == 403
    )
    headers = {"Authorization": "Bearer other-factory-token-long-enough", "X-Tenant-ID": "demo"}
    assert client.get("/api/v1/sources", headers=headers).json() == []
    assert client.get("/api/v1/ontologies", headers=headers).json() == []
    assert client.get("/api/v1/tools", headers=headers).json() == {"tools": []}
    for path in ["/api/v1/invoke/read_order", "/api/v1/mcp/invoke/read_order"]:
        assert client.post(path, json={"entity_id": "SO-2026-0088"}, headers=headers).status_code == 404
    assert (
        client.get("/api/v1/ontologies/" + ontology["id"] + "/snapshot", headers=headers).status_code == 404
    )
    # Same projection and denial on REST and the gateway's MCP execution route.
    rest = api(client, "POST", "/invoke/read_order", {"entity_id": "SO-2026-0088"}, role="scoped")
    mcp = api(client, "POST", "/mcp/invoke/read_order", {"entity_id": "SO-2026-0088"}, role="scoped")
    assert rest["result"] == mcp["result"] and mcp["meta"]["provenance"] == []
    assert any(c["transport"] == "mcp" for c in api(client, "GET", "/capability-operations")["calls"])


def test_admin_self_approval_is_denied_at_capability_and_action_boundaries(platform):
    _, client, ontology, _ = platform
    api(
        client,
        "POST",
        "/capabilities",
        {
            "name": "admin_draft",
            "title": "排产草案",
            "description": "管理员同样不能自行批准",
            "ontology_id": ontology["id"],
            "ontology_version": 1,
            "template": "schedule_draft",
            "roles": ["admin", "approver"],
        },
        role="admin",
        code=201,
    )
    api(client, "POST", "/capabilities/admin_draft/generate", role="admin")
    assert api(client, "POST", "/capabilities/admin_draft/test", role="admin")["passed"]
    api(client, "POST", "/capabilities/admin_draft/publish", role="admin", code=403)
    api(client, "POST", "/capabilities/admin_draft/publish", role="approver")
    result = api(
        client,
        "POST",
        "/invoke/admin_draft",
        {
            "order_id": "SO-2026-0088",
            "extra_hours": 2,
            "note": "管理员提交待复核排产草案",
            "idempotency_key": "admin-proposal-001",
        },
        role="admin",
    )
    api(
        client,
        "POST",
        "/actions/" + result["result"]["action_id"] + "/decision",
        {"decision": "approve", "reason": "试图自行批准"},
        role="admin",
        code=403,
    )


def test_manufacturing_templates_require_published_relation_semantics(platform):
    _, client, ontology, version = platform
    record = next(o for o in api(client, "GET", "/ontologies") if o["id"] == ontology["id"])
    spec = deepcopy(record["spec"])
    spec["relations"] = [r for r in spec["relations"] if r["from_field"] != "order_id"]
    api(
        client,
        "POST",
        "/ontologies/" + ontology["id"] + "/draft",
        {"revision": record["revision"], "spec": spec},
    )
    published = api(
        client,
        "POST",
        "/ontologies/" + ontology["id"] + "/publish",
        {"revision": record["revision"] + 1},
        role="approver",
    )
    body = {
        "name": "missing_relationship",
        "title": "Incomplete graph",
        "description": "Should not guess work order joins",
        "ontology_id": ontology["id"],
        "ontology_version": published["version"],
        "template": "production_progress",
    }
    api(client, "POST", "/capabilities", body, code=409)
    # A generic lookup may still use a graph without that business-specific relationship.
    api(client, "POST", "/capabilities", {**body, "template": "entity_lookup"}, code=201)
