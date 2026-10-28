import csv
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from ontobridge.api import create_app
from ontobridge.connectors import allowed_path, database


@pytest.fixture
def platform(tmp_path):
    app = create_app(tmp_path, demo=True, credentials={})
    with TestClient(app) as client:
        tokens = {
            role: client.post("/api/demo-session/" + role).json()["token"]
            for role in ("operator", "viewer", "approver")
        }

        def request(method, path, body=None, role="operator"):
            return client.request(
                method, "/api" + path, json=body, headers={"Authorization": "Bearer " + tokens[role]}
            )

        yield app, client, request, tmp_path


def call(request, name, **args):
    return request("POST", f"/capabilities/{name}/call", args)


def rewrite_csv(path, mutate):
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fields, rows = reader.fieldnames, list(reader)
    rows = mutate(rows)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def proposal(request, key="idempotency-001"):
    return call(
        request,
        "propose_schedule_change",
        order_id="SO-2026-0088",
        note="业务负责人请求调整产能",
        extra_hours=2,
        idempotency_key=key,
    )


def test_seed_and_all_capabilities_have_provenance(platform):
    app, client, request, root = platform
    data = request("GET", "/overview").json()
    assert len(data["orders"]) == 8 and data["object_count"] == 52 and not data["issues"]
    for name in [
        "list_due_orders",
        "get_order_360",
        "check_delivery_risk",
        "get_material_shortage",
        "get_production_progress",
        "simulate_schedule",
    ]:
        response = call(request, name, order_id="SO-2026-0088", extra_hours=2)
        assert response.status_code == 200, response.text
        assert response.json()["meta"]["provenance"]
        assert not response.json()["meta"]["warnings"]
    result = call(request, "get_order_360", order_id="SO-2026-0088").json()["result"]
    assert result["progress"]["finished"] == 864
    assert result["progress"]["allocation_valid"]
    assert result["materials"]["required"] == 604.8
    assert result["risk"]["production_days"] == 3
    assert result["risk"]["delay_days"] == 1
    assert result["risk"]["level"] == "high"


def test_sync_idempotent_and_source_is_readonly(platform):
    app, _, request, root = platform
    source = root / "sources/demo/erp.sqlite"
    before = source.read_bytes()
    for _ in range(2):
        r = request("POST", "/sources/src-erp/sync", {})
        assert r.status_code == 200 and r.json()["changed"] == 0
    assert before == source.read_bytes()
    conn = database(source)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM sale_order")
    conn.close()


def test_sync_atomic_rollback_and_error_audit(platform):
    app, _, request, root = platform
    before = request("GET", "/ontology").json()["objects"]
    path = root / "sources/demo/mes_work_orders.csv"

    def mutate(rows):
        rows[0]["finished_qty"] = "100"
        rows[-1]["finished_qty"] = "-5"
        return rows

    rewrite_csv(path, mutate)
    response = request("POST", "/sources/src-mes/sync", {})
    assert response.status_code == 422
    assert request("GET", "/ontology").json()["objects"] == before
    ops = request("GET", "/operations").json()
    assert ops["runs"][0]["status"] == "failed"
    assert (
        call(request, "check_delivery_risk", order_id="SO-2026-0088").json()["result"]["level"] == "unknown"
    )


def test_mapping_confirm_version_and_staleness(platform):
    app, _, request, root = platform
    mapping = request("GET", "/mappings").json()[0]
    body = {k: mapping[k] for k in ["source_id", "table", "entity", "fields"]}
    assert request("POST", "/mappings/preview", body).json()["error_count"] == 0
    result = request("POST", "/mappings", body).json()
    assert result["version"] == 2 and result["status"] == "draft"
    assert (
        call(request, "check_delivery_risk", order_id="SO-2026-0088").json()["result"]["level"] == "unknown"
    )
    assert request("POST", "/sources/src-erp/sync", {}).status_code == 422
    assert request("POST", f"/mappings/{result['id']}/confirm", {}).status_code == 200
    assert request("POST", "/sources/src-erp/sync", {}).status_code == 200
    assert call(request, "check_delivery_risk", order_id="SO-2026-0088").json()["result"]["level"] == "high"


def test_shared_stock_not_double_counted(platform):
    _, _, request, _ = platform
    first = call(request, "get_material_shortage", order_id="SO-2026-0090").json()["result"]
    second = call(request, "get_material_shortage", order_id="SO-2026-0093").json()["result"]
    assert first["shortage"] == 648
    assert second["available"] == 0 and second["prior_allocations"] == 1248
    assert second["shortage"] == 451.2  # Each work order reports 187 completed units (integer truncation).


def test_stale_source_mtime_and_proposal_precheck(platform):
    _, _, request, root = platform
    path = root / "sources/demo/mes_work_orders.csv"
    old = (datetime.now(UTC) - timedelta(days=3)).timestamp()
    os.utime(path, (old, old))
    assert request("POST", "/sources/src-mes/sync", {}).status_code == 200
    result = call(request, "check_delivery_risk", order_id="SO-2026-0088").json()
    assert result["result"]["level"] == "unknown" and result["meta"]["warnings"]
    assert proposal(request).status_code == 422


def test_draft_idempotency_roles_and_approval_state(platform):
    app, client, request, root = platform
    first = proposal(request)
    assert first.status_code == 200, first.text
    action = first.json()["result"]
    assert proposal(request).json()["result"]["id"] == action["id"]
    assert len(request("GET", "/actions").json()) == 1
    assert (
        call(
            request,
            "propose_schedule_change",
            order_id="SO-2026-0088",
            note="不同业务内容",
            idempotency_key="idempotency-001",
        ).status_code
        == 409
    )
    url = f"/actions/{action['id']}/decision"
    body = {"decision": "approve", "reason": "经核实可以保留此排产建议"}
    assert request("POST", url, body).status_code == 403
    approved = request("POST", url, body, role="approver")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved_draft"
    assert approved.json()["executed"] is False
    assert request("POST", url, body, role="approver").status_code == 409
    assert request("POST", "/sources/src-erp/sync", {}, role="viewer").status_code == 403
    assert request("POST", "/capabilities/propose_schedule_change/call", {}, role="viewer").status_code == 403
    assert request("GET", "/sources/src-erp/schema", role="viewer").status_code == 403
    assert call(request, "get_order_360", order_id="NOT-FOUND").status_code == 404
    assert client.get("/api/overview").status_code == 401


def test_approval_rejects_changed_input(platform):
    _, _, request, root = platform
    action = proposal(request).json()["result"]
    rewrite_csv(
        root / "sources/demo/mes_work_orders.csv", lambda rows: [dict(rows[0], finished_qty="100"), *rows[1:]]
    )
    assert request("POST", "/sources/src-mes/sync", {}).status_code == 200
    r = request(
        "POST",
        f"/actions/{action['id']}/decision",
        {"decision": "approve", "reason": "确认审批内容"},
        role="approver",
    )
    assert r.status_code == 409
    assert (
        request(
            "POST",
            f"/actions/{action['id']}/decision",
            {"decision": "reject", "reason": "来源已变更需重新申请"},
            role="approver",
        ).status_code
        == 200
    )


def test_review_correction_invalidated_by_source_change(platform):
    _, _, request, root = platform
    path = root / "sources/demo/mes_work_orders.csv"
    rewrite_csv(path, lambda rows: [dict(rows[0], order_ref="SO-MISSING"), *rows[1:]])
    assert request("POST", "/sources/src-mes/sync", {}).status_code == 200
    ontology = request("GET", "/ontology").json()
    issue = ontology["issues"][0]
    assert issue["source_key"] == "MO-00231"
    target = next(
        o["id"]
        for o in ontology["objects"]
        if o["entity"] == "SalesOrder" and o["data"]["id"] == "SO-2026-0088"
    )
    assert proposal(request).status_code == 422
    assert (
        request(
            "POST", "/resolutions/" + issue["id"], {"target_id": target, "reason": "根据原始拆单记录确认"}
        ).status_code
        == 200
    )
    assert not request("GET", "/ontology").json()["issues"]
    rewrite_csv(path, lambda rows: [dict(rows[0], finished_qty="111"), *rows[1:]])
    assert request("POST", "/sources/src-mes/sync", {}).status_code == 200
    assert request("GET", "/ontology").json()["issues"]


def test_deleted_and_reappearing_records_keep_history(platform):
    app, _, request, root = platform
    path = root / "sources/demo/wms_stock.csv"
    saved = path.read_text()
    obj = next(
        o
        for o in request("GET", "/ontology").json()["objects"]
        if o["entity"] == "Inventory" and o["data"]["id"] == "INV-01"
    )
    rewrite_csv(path, lambda rows: rows[1:])
    assert request("POST", "/sources/src-wms/sync", {}).json()["removed"] == 1
    path.write_text(saved)
    assert request("POST", "/sources/src-wms/sync", {}).status_code == 200
    history = request("GET", f"/objects/{obj['id']}/history").json()
    assert [r["version"] for r in history] == [1, 2, 3]
    assert [r["event"] for r in history] == ["upsert", "removed", "upsert"]


def test_paths_origin_validation_and_duplicate_ids(platform, tmp_path):
    app, client, request, root = platform
    assert (
        client.post("/api/demo-session/operator", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert client.get("/api/config", headers={"Host": "evil.example"}).status_code == 403
    for path in ["../ontobridge.sqlite", "/etc/passwd"]:
        r = request("POST", "/sources", {"name": "bad", "system": "ERP", "kind": "sqlite", "path": path})
        assert r.status_code == 422
    external = root / "outside.csv"
    external.write_text("id\n1\n")
    (root / "sources/link.csv").symlink_to(external)
    with pytest.raises(ValueError):
        allowed_path(root / "sources", "link.csv")
    path = root / "sources/demo/mes_work_orders.csv"
    rewrite_csv(path, lambda rows: [*rows, rows[0]])
    assert request("POST", "/sources/src-mes/sync", {}).status_code == 422
    assert call(request, "simulate_schedule", extra_hours=99).status_code == 422
    assert call(request, "get_order_360", arbitrary_sql="DROP TABLE anything").status_code == 422


def test_new_csv_connector_mapping_and_collision(platform):
    _, _, request, root = platform
    path = root / "sources/extra.csv"
    path.write_text("code,title,measure\nMAT-04,纽扣,粒\n")
    source = request(
        "POST", "/sources", {"name": "新物料文件", "system": "ERP", "kind": "csv", "path": "extra.csv"}
    ).json()
    body = {
        "source_id": source["id"],
        "table": "extra",
        "entity": "Material",
        "fields": {"id": "code", "name": "title", "unit": "measure"},
    }
    assert request("POST", "/mappings/preview", body).json()["valid_count"] == 1
    mapping = request("POST", "/mappings", body).json()
    assert request("POST", f"/sources/{source['id']}/sync", {}).status_code == 422
    assert request("POST", f"/mappings/{mapping['id']}/confirm", {}).status_code == 200
    assert request("POST", f"/sources/{source['id']}/sync", {}).status_code == 200
    path.write_text("code,title,measure\nMAT-01,重复主键,粒\n")
    assert request("POST", f"/sources/{source['id']}/sync", {}).status_code == 422


def test_persistence_and_isolation(tmp_path):
    token = "test-only-token-of-sufficient-length"
    credentials = {token: {"user": "reader", "role": "viewer", "tenant": "company-a"}}
    app = create_app(tmp_path, demo=False, credentials=credentials)
    with app.state.store.transaction("company-b") as tx:
        tx.put("source", "secret-source", {"id": "secret-source", "name": "company-b secret"})
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + token}
        assert client.get("/api/sources", headers=headers).json() == []
        assert client.get("/api/ontology", headers=headers).json()["objects"] == []
        assert client.post("/api/demo-session/operator").status_code == 404
    app2 = create_app(tmp_path, demo=False, credentials=credentials)
    with app2.state.store.transaction("company-b") as tx:
        assert tx.get("source", "secret-source")["name"] == "company-b secret"
    app2.state.store.engine.dispose()
    with pytest.raises(RuntimeError):
        create_app(tmp_path, demo=False, credentials={})


def test_admin_cannot_approve_own_draft(tmp_path):
    from ontobridge.demo import seed

    token = "admin-token-for-self-approval-test"
    app = create_app(
        tmp_path, demo=False, credentials={token: {"user": "admin-user", "role": "admin", "tenant": "demo"}}
    )
    seed(app.state.store, app.state.source_root)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + token
        action = client.post(
            "/api/capabilities/propose_schedule_change/call",
            json={"order_id": "SO-2026-0088", "note": "管理员申请调整", "idempotency_key": "admin-test-key"},
        ).json()["result"]
        response = client.post(
            "/api/actions/" + action["id"] + "/decision",
            json={"decision": "approve", "reason": "管理员自行审核"},
        )
        assert response.status_code == 403
        assert client.get("/api/actions").json()[0]["status"] == "pending_approval"


def test_csv_schema_and_size_bounds(platform):
    _, _, request, root = platform
    for contents in ["id,id\nA,A\n", "id,name\nA\n", "id,name\nA,Name,Extra\n", "id\n" + "A\n" * 10001]:
        (root / "sources/bad.csv").write_text(contents)
        response = request(
            "POST", "/sources", {"name": "Invalid CSV", "system": "OTHER", "kind": "csv", "path": "bad.csv"}
        )
        assert response.status_code == 422


def test_concurrent_duplicate_drafts_are_serialized(platform):
    from concurrent.futures import ThreadPoolExecutor

    _, _, request, _ = platform
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: proposal(request, "concurrent-request-key"), range(4)))
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()["result"]["id"] for r in results}) == 1
    assert len(request("GET", "/actions").json()) == 1
