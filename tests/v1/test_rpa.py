import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from ontobridge.service import DomainError
from ontobridge.v1.connector_sdk import ConnectorContext
from ontobridge.v1.rpa import WebRPAConnector, WorkflowSpec, allowed_request


@pytest.fixture
def erp_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/export"):
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Disposition", 'attachment; filename="orders.csv"')
                self.end_headers()
                self.wfile.write(b"id,customer\nSO-88,Mock Factory\n")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b'<html><input type="password" id="password" name="password"/><input id="date"/><a id="export" href="/export">Export</a><button id="write" onclick="fetch(\'/write\',{method:\'POST\'})">Write</button></html>'
                )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def test_real_browser_export_and_snapshot_reuse(tmp_path, erp_server):
    config = {
        "kind": "web_rpa",
        "base_url": erp_server,
        "allowed_read_paths": ["/", "/export", "/favicon.ico"],
    }
    source = {
        "kind": "web_rpa",
        "connection_ref": "erp",
        "_run_id": "run-test",
        "_approved_workflow": {
            "name": "Mock ERP export",
            "connection_ref": "erp",
            "status": "approved",
            "expected_columns": ["id", "customer"],
            "output_format": "csv",
            "steps": [
                {"action": "navigate", "path": "/"},
                {"action": "fill", "selector": "#date", "value": "2026-10-29"},
                {"action": "download", "selector": "#export"},
            ],
        },
    }
    connector = WebRPAConnector(ConnectorContext(tmp_path, "factory", source, {"factory": {"erp": config}}))
    schema = connector.discover()
    assert schema[0].name == "export"
    batch = connector.read("export", "id", 10)
    assert batch.rows[0]["id"] == "SO-88" and batch.exhausted
    receipt = tmp_path / "factory/.exports/run-test/receipt.json"
    stamp = receipt.stat().st_mtime_ns
    assert connector.read("export", "id", 10).rows == batch.rows
    assert receipt.stat().st_mtime_ns == stamp
    export = receipt.parent / "export.csv"
    export.write_text("id,customer\nSO-TAMPERED,Bad\n")
    with pytest.raises(DomainError, match="指纹"):
        connector.read("export", "id", 10)


def test_unapproved_and_cross_origin_workflows_blocked(tmp_path, erp_server):
    config = {"kind": "web_rpa", "base_url": erp_server, "allowed_read_paths": ["/", "/export"]}
    assert not allowed_request(erp_server + "/write", "POST", config)
    assert not allowed_request("https://example.com/export", "GET", config)
    assert not allowed_request(erp_server + "/admin", "GET", config)
    connector = WebRPAConnector(
        ConnectorContext(
            tmp_path, "factory", {"kind": "web_rpa", "connection_ref": "erp"}, {"factory": {"erp": config}}
        )
    )
    with pytest.raises(DomainError, match="未批准"):
        connector.discover()
    with pytest.raises(ValueError):
        WorkflowSpec.model_validate(
            {
                "name": "Bad",
                "connection_ref": "erp",
                "expected_columns": ["id"],
                "steps": [
                    {"action": "navigate", "path": "//evil.example/"},
                    {"action": "download", "selector": "#export"},
                ],
            }
        )


def test_record_review_export_and_separate_cache(tmp_path, erp_server, monkeypatch):
    import json

    from fastapi.testclient import TestClient
    from ontobridge.api import create_app
    from ontobridge.v1.recorder import Recording
    from playwright.sync_api import sync_playwright

    recording = Recording("/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        recording.attach(context, page)
        page.goto(erp_server)
        page.locator("#password").fill("never-record-this-secret")
        page.locator("#date").click()
        page.locator("#date").fill("2026-10-29")
        with page.expect_download():
            page.locator("#export").click()
        page.wait_for_timeout(100)
        browser.close()
    assert "never-record-this-secret" not in json.dumps(recording.steps)
    assert recording.steps[-1]["action"] == "download"
    assert any(step.get("secret_ref") == "password" for step in recording.steps)
    spec = {
        "name": "Recorded export",
        "connection_ref": "erp",
        "expected_columns": ["id", "customer"],
        "output_format": recording.downloaded[0],
        "steps": recording.steps,
    }
    operator, approver = "rpa-operator-token-long-enough", "rpa-approver-token-long-enough"
    app = create_app(
        tmp_path / "platform",
        demo=False,
        credentials={
            operator: {"user": "author", "role": "operator", "tenant": "factory"},
            approver: {"user": "reviewer", "role": "approver", "tenant": "factory"},
        },
    )
    app.state.connector_profiles = {
        "factory": {
            "erp": {
                "kind": "web_rpa",
                "base_url": erp_server,
                "allowed_read_paths": ["/", "/export", "/favicon.ico"],
                "secrets": {"password": "not-in-workflow"},
            }
        }
    }
    monkeypatch.setenv("ONTOBRIDGE_CACHE_ROOT", str(tmp_path / "cache"))
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + operator
        workflow = client.post("/api/v1/rpa/workflows", json=spec)
        assert workflow.status_code == 201, workflow.text
        workflow_id = workflow.json()["id"]
        assert client.post("/api/v1/rpa/workflows/" + workflow_id + "/approve").status_code == 403
        source = client.post(
            "/api/v1/sources",
            json={
                "name": "Recorded ERP",
                "kind": "web_rpa",
                "system": "ERP",
                "connection_ref": "erp",
                "options": {"workflow_id": workflow_id},
            },
        ).json()
        url = "/api/v1/sources/" + source["id"] + "/discover"
        assert client.post(url).status_code == 403
        assert (
            client.post(
                "/api/v1/rpa/workflows/" + workflow_id + "/approve",
                headers={"Authorization": "Bearer " + approver},
            ).status_code
            == 200
        )
        response = client.post(url)
        assert response.status_code == 200, response.text
        assert response.json()[0]["sample"][0]["id"] == "SO-88"
        assert list((tmp_path / "cache/factory/.exports").glob("*/receipt.json"))
        assert not list(app.state.source_root.glob("**/export.csv"))
