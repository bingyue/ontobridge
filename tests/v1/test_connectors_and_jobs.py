import sqlite3

import pytest
from fastapi.testclient import TestClient
from ontobridge.api import create_app
from ontobridge.service import DomainError
from ontobridge.store import Store
from ontobridge.v1.connector_sdk import ConnectorContext, ConnectorRegistry
from ontobridge.v1.db_connector import DatabaseConnector
from ontobridge.v1.file_connectors import FileConnector
from ontobridge.v1.pipelines import GatewayWorker
from openpyxl import Workbook


@pytest.fixture
def platform(tmp_path):
    token = "v1-tests-operator-token-long-enough"
    app = create_app(
        tmp_path,
        demo=False,
        credentials={token: {"user": "engineer", "tenant": "factory", "role": "operator"}},
    )
    folder = app.state.source_root / "factory"
    folder.mkdir()
    conn = sqlite3.connect(folder / "erp.sqlite")
    conn.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT NOT NULL, updated INTEGER NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO orders VALUES (?,?,?)",
        [(1, "A", 10), (2, "B", 10), (3, "C", 11), (4, "D", 12), (5, "E", 12)],
    )
    conn.commit()
    conn.close()
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + token
        yield app, client, folder


def pipeline(app, client, mode="incremental", kind="sqlite", path="erp.sqlite", table="orders", pk="id"):
    source = client.post(
        "/api/v1/sources", json={"name": "ERP fixture", "kind": kind, "system": "ERP", "path": path}
    ).json()
    discovery = client.post("/api/v1/sources/" + source["id"] + "/discover")
    assert discovery.status_code == 200, discovery.text
    body = {
        "name": "Order ingest",
        "source_id": source["id"],
        "table": table,
        "primary_key": pk,
        "mode": mode,
        "batch_size": 2,
        "retry_seconds": 1,
    }
    if mode == "incremental":
        body["cursor"] = {"column": "updated", "tie_breaker": pk, "kind": "integer"}
    response = client.post("/api/v1/pipelines", json=body)
    assert response.status_code == 201, response.text
    return source, response.json()


def execute(app, client, p, worker=None):
    run = client.post("/api/v1/pipelines/" + p["id"] + "/runs").json()
    worker = worker or GatewayWorker(app.state.store, app.state.source_root, app.state.connector_registry)
    for _ in range(20):
        worker.tick("factory")
        state = client.get("/api/v1/runs/" + run["id"]).json()
        if state["status"] in ("failed", "succeeded", "retry_wait"):
            return state
    raise AssertionError("job did not reach a terminal or retry state")


def test_registry_and_readonly_discovery(platform):
    app, client, folder = platform
    result = client.get("/api/v1/connectors").json()
    assert {"postgresql", "mysql", "mssql", "sqlite", "excel", "csv", "sftp"} <= {c["kind"] for c in result}
    source, _ = pipeline(app, client)
    assert client.post("/api/v1/sources/" + source["id"] + "/test").json()["read_only"]
    schema = client.post("/api/v1/sources/" + source["id"] + "/discover").json()[0]
    assert schema["primary_key"] == ["id"] and schema["columns"][0]["primary_key"]
    connector = DatabaseConnector(ConnectorContext(app.state.source_root, "factory", source))
    with pytest.raises(DomainError):
        with connector.connection() as conn:
            conn.exec_driver_sql("DELETE FROM orders")
    registry = ConnectorRegistry()
    registry.register(DatabaseConnector)
    with pytest.raises(ValueError):
        registry.register(DatabaseConnector)


def test_atomic_promotion_and_equal_watermark_updates(platform):
    app, client, folder = platform
    _, p = pipeline(app, client)
    run = client.post("/api/v1/pipelines/" + p["id"] + "/runs").json()
    worker = GatewayWorker(app.state.store, app.state.source_root, app.state.connector_registry)
    worker.tick("factory")
    assert client.get("/api/v1/assets").json()["count"] == 0
    assert client.get("/api/v1/pipelines").json()[0]["checkpoint"] is None
    for _ in range(3):
        worker.tick("factory")
    assert client.get("/api/v1/runs/" + run["id"]).json()["status"] == "succeeded"
    assert client.get("/api/v1/assets").json()["count"] == 5
    db = sqlite3.connect(folder / "erp.sqlite")
    db.execute("UPDATE orders SET customer='D-UPDATED' WHERE id=4")  # Same maximum watermark, lower PK.
    db.execute("UPDATE orders SET customer='B-UPDATED',updated=13 WHERE id=2")
    db.execute("INSERT INTO orders VALUES (6,'F',13)")
    db.commit()
    db.close()
    state = execute(app, client, p)
    assert state["status"] == "succeeded" and state["changed"] == 3
    rows = {a["data"]["id"]: a["data"] for a in client.get("/api/v1/assets").json()["items"]}
    assert len(rows) == 6 and rows[4]["customer"] == "D-UPDATED" and rows[2]["customer"] == "B-UPDATED"
    assert execute(app, client, p)["changed"] == 0


def test_worker_restart_resume_and_exclusive_lease(platform):
    app, client, folder = platform
    _, p = pipeline(app, client)
    run = client.post("/api/v1/pipelines/" + p["id"] + "/runs").json()
    worker = GatewayWorker(
        app.state.store, app.state.source_root, app.state.connector_registry, worker_id="first"
    )
    worker.tick("factory")
    other_store = Store(str(app.state.store.engine.url))
    other = GatewayWorker(
        other_store, app.state.source_root, app.state.connector_registry, worker_id="second"
    )
    assert not other.tick("factory")
    with app.state.store.transaction("factory") as tx:
        state = tx.get("v1_run", run["id"])
        state["lease_until"] = "2000-01-01T00:00:00+00:00"
        tx.put("v1_run", run["id"], state)
    for _ in range(3):
        other.tick("factory")
    state = client.get("/api/v1/runs/" + run["id"]).json()
    assert state["status"] == "succeeded" and state["attempts"] == 2 and state["staged"] == 5
    assert client.get("/api/v1/assets").json()["count"] == 5
    other_store.engine.dispose()


def test_disconnect_retry_does_not_publish_partial_data(platform):
    app, client, folder = platform
    _, p = pipeline(app, client)
    run = client.post("/api/v1/pipelines/" + p["id"] + "/runs").json()
    worker = GatewayWorker(app.state.store, app.state.source_root, app.state.connector_registry)
    worker.tick("factory")
    (folder / "erp.sqlite").rename(folder / "offline.sqlite")
    worker.tick("factory")
    assert client.get("/api/v1/runs/" + run["id"]).json()["status"] == "retry_wait"
    assert client.get("/api/v1/assets").json()["count"] == 0
    assert client.get("/api/v1/operations").json()["alerts"][0]["status"] == "open"
    (folder / "offline.sqlite").rename(folder / "erp.sqlite")
    with app.state.store.transaction("factory") as tx:
        state = tx.get("v1_run", run["id"])
        state["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
        tx.put("v1_run", run["id"], state)
    for _ in range(3):
        worker.tick("factory")
    assert client.get("/api/v1/runs/" + run["id"]).json()["status"] == "succeeded"
    assert client.get("/api/v1/operations").json()["alerts"][0]["status"] == "resolved"


def test_full_reconciliation_removes_deleted_rows(platform):
    app, client, folder = platform
    _, p = pipeline(app, client, mode="full")
    assert execute(app, client, p)["status"] == "succeeded"
    db = sqlite3.connect(folder / "erp.sqlite")
    db.execute("DELETE FROM orders WHERE id=3")
    db.commit()
    db.close()
    assert execute(app, client, p)["removed"] == 1
    assert client.get("/api/v1/assets").json()["count"] == 4


def test_excel_and_file_changed_during_resume(platform):
    app, client, folder = platform
    book = Workbook()
    sheet = book.active
    sheet.title = "Orders"
    sheet.append(["id", "name"])
    for i in range(6):
        sheet.append([str(i), "order-" + str(i)])
    book.save(folder / "orders.xlsx")
    _, p = pipeline(app, client, mode="full", kind="excel", path="orders.xlsx", table="Orders")
    worker = GatewayWorker(app.state.store, app.state.source_root, app.state.connector_registry)
    run = client.post("/api/v1/pipelines/" + p["id"] + "/runs").json()
    worker.tick("factory")
    sheet.append(["6", "new"])
    book.save(folder / "orders.xlsx")
    worker.tick("factory")
    state = client.get("/api/v1/runs/" + run["id"]).json()
    assert state["status"] == "retry_wait" and state["staged"] == 0
    assert client.get("/api/v1/assets").json()["count"] == 0
    with app.state.store.transaction("factory") as tx:
        state["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
        tx.put("v1_run", state["id"], state)
    for _ in range(5):
        worker.tick("factory")
    assert client.get("/api/v1/assets").json()["count"] == 7


def test_formula_and_tenant_path_escape_rejected(platform):
    app, client, folder = platform
    book = Workbook()
    sheet = book.active
    sheet.append(["id", "value"])
    sheet.append(["1", "=1+1"])
    book.save(folder / "formula.xlsx")
    source = {"kind": "excel", "path": "formula.xlsx"}
    connector = FileConnector(ConnectorContext(app.state.source_root, "factory", source))
    with pytest.raises(DomainError, match="公式"):
        connector.discover()
    for path in ["../../ontobridge.sqlite", "/etc/passwd"]:
        with pytest.raises(DomainError):
            FileConnector(
                ConnectorContext(app.state.source_root, "factory", {"kind": "csv", "path": path})
            ).discover()


def test_pipeline_contract_and_no_duplicate_jobs(platform):
    app, client, _ = platform
    _, p = pipeline(app, client)
    first = client.post("/api/v1/pipelines/" + p["id"] + "/runs").json()
    second = client.post("/api/v1/pipelines/" + p["id"] + "/runs").json()
    assert first["id"] == second["id"]
    assert (
        client.post(
            "/api/v1/pipelines",
            json={
                **{k: v for k, v in p.items() if k not in ("id", "version", "created_at", "checkpoint")},
                "primary_key": "missing",
            },
        ).status_code
        == 422
    )


def test_repeated_worker_crashes_exhaust_lease_retry_budget(platform):
    app, client, _ = platform
    source, p = pipeline(app, client)
    run = client.post("/api/v1/pipelines/" + p["id"] + "/runs").json()
    for i in range(p["max_attempts"]):
        worker = GatewayWorker(
            app.state.store, app.state.source_root, app.state.connector_registry, worker_id=str(i)
        )
        assert worker.claim("factory")
        with app.state.store.transaction("factory") as tx:
            record = tx.get("v1_run", run["id"])
            record["lease_until"] = "2000-01-01T00:00:00+00:00"
            tx.put("v1_run", run["id"], record)
    worker = GatewayWorker(app.state.store, app.state.source_root, app.state.connector_registry)
    assert worker.claim("factory") is None
    state = client.get("/api/v1/runs/" + run["id"]).json()
    assert state["status"] == "failed" and state["attempts"] == p["max_attempts"]
    assert client.get("/api/v1/operations").json()["alerts"][0]["status"] == "open"
    assert client.get("/api/v1/assets").json()["count"] == 0
    assert client.get("/api/me").json()["legacy_access"] is False
    assert client.get("/api/overview").status_code == 403
    assert client.post("/api/capabilities/get_order_360/call", json={"order_id": "SO-88"}).status_code == 403


def test_installed_entrypoint_connector_is_usable_via_public_api(platform, monkeypatch):
    from types import SimpleNamespace

    from ontobridge.v1.connector_sdk import Connector, timestamp
    from ontobridge.v1.contracts import ReadBatch, SourceColumn, TableSchema

    class FixtureConnector(Connector):
        kinds = ("fixture_plugin",)

        def discover(self):
            return [
                TableSchema(
                    name="custom",
                    primary_key=["id"],
                    columns=[SourceColumn(name="id", type="string", primary_key=True)],
                )
            ]

        def read(self, table, primary_key, batch_size, cursor=None, checkpoint=None):
            return ReadBatch(
                rows=[{"id": "plugin-001"}],
                checkpoint={"key": "plugin-001"},
                exhausted=True,
                source_updated_at=timestamp(),
                freshness_basis="synthetic_plugin",
            )

    app, client, _ = platform
    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda **kw: (
            [SimpleNamespace(load=lambda: FixtureConnector)] if kw["group"] == "ontobridge.connectors" else []
        ),
    )
    app.state.connector_registry.load_entry_points()
    body = {"name": "Plugin fixture", "kind": "fixture_plugin", "system": "OTHER"}
    source = client.post("/api/v1/sources", json=body)
    assert source.status_code == 201, source.text
    assert client.post("/api/v1/sources/" + source.json()["id"] + "/discover").status_code == 200
    result = client.post(
        "/api/v1/pipelines",
        json={
            "name": "Custom ingest",
            "source_id": source.json()["id"],
            "table": "custom",
            "primary_key": "id",
        },
    )
    assert result.status_code == 201, result.text
    assert execute(app, client, result.json())["status"] == "succeeded"
    assert client.get("/api/v1/assets").json()["items"][0]["data"] == {"id": "plugin-001"}
    assert client.post("/api/v1/sources", json={**body, "kind": "not_installed"}).status_code == 422
