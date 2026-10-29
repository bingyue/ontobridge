"""Isolated private-mode PostgreSQL + API + Worker + MCP acceptance, using synthetic data only.
Creates only the ontobridge-v1-acceptance Compose project; leaves it running for review.
Requires built console/mcp images. Credentials are never printed and existing volumes are preserved.
"""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "var/container-acceptance"
PROJECT = "ontobridge-v1-acceptance"


def run(command, **kwargs):
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError("Acceptance command failed: " + result.stderr[-1500:])
    return result.stdout


def write_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    envfile, authfile = STATE / ".env", STATE / "access.json"
    if not envfile.exists():
        assert not authfile.exists(), "Preserve existing credentials and resolve partial initialization"
        credentials = {r: secrets.token_urlsafe(32) for r in ["operator", "approver", "viewer"]}
        principals = {
            token: {"user": role + ".mock", "role": role, "tenant": "demo"}
            for role, token in credentials.items()
        }
        write_private(authfile, json.dumps(credentials))
        write_private(
            envfile,
            "ONTOBRIDGE_PORT=18787\nONTOBRIDGE_MCP_PORT=18790\nPOSTGRES_PASSWORD="
            + secrets.token_urlsafe(32)
            + "\nONTOBRIDGE_TOKENS='"
            + json.dumps(principals)
            + "'\n",
        )
    fixture_volume = PROJECT + "-imports"
    override = STATE / "compose.json"
    override.write_text(
        json.dumps(
            {
                "services": {
                    name: {
                        "volumes": [
                            {
                                "type": "volume",
                                "source": "mock-imports",
                                "target": "/data/sources",
                                "read_only": True,
                            },
                            {
                                "type": "volume",
                                "source": "mock-config",
                                "target": "/run/ontobridge",
                                "read_only": True,
                            },
                        ]
                    }
                    for name in ["console", "worker"]
                },
                "volumes": {"mock-imports": {"name": fixture_volume}, "mock-config": {}},
            }
        )
    )
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(envfile),
        "-p",
        PROJECT,
        "-f",
        "compose.yaml",
        "-f",
        str(override),
    ]
    run([*compose, "up", "-d", "--wait", "postgres"])
    seed_code = """import json, os
from pathlib import Path
from ontobridge.store import Store
from ontobridge.demo import seed
store=Store(os.environ["ONTOBRIDGE_DATABASE_URL"])
seed(store, Path("/data/sources"))
with store.transaction("demo") as tx:
 print(json.dumps({"synthetic_fixture":"ontobridge-manufacturing-v1","sources":tx.all("source"),"mappings":tx.all("mapping")}))
store.engine.dispose()
"""
    password = next(
        line.split("=", 1)[1]
        for line in envfile.read_text().splitlines()
        if line.startswith("POSTGRES_PASSWORD=")
    )
    seed_env = {
        **os.environ,
        "ONTOBRIDGE_DATABASE_URL": f"postgresql+psycopg://ontobridge:{password}@postgres:5432/ontobridge",
    }
    manifest = run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0",
            "--network",
            PROJECT + "_default",
            "--volume",
            fixture_volume + ":/data/sources:rw",
            "-e",
            "ONTOBRIDGE_DATABASE_URL",
            "--entrypoint",
            "/app/.venv/bin/python",
            "ontobridge-platform:local",
            "-c",
            seed_code,
        ],
        env=seed_env,
    )
    fixture = STATE / "fixture.json"
    fixture.write_text(json.dumps(json.loads(manifest)))
    run([*compose, "up", "-d", "--no-build", "console", "worker", "mcp"])
    url = "http://127.0.0.1:18787"
    deadline = time.monotonic() + 60
    while True:
        try:
            response = httpx.get(url + "/api/health", timeout=2)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("Private API did not become healthy")
        time.sleep(0.5)
    assert httpx.get(url + "/api/config").json()["demo"] is False
    assert httpx.post(url + "/api/demo-session/operator").status_code == 404
    report = run(
        [
            sys.executable,
            "scripts/v1_mock_acceptance.py",
            "--url",
            url,
            "--credentials",
            str(authfile),
            "--fixture-manifest",
            str(fixture),
            "--receipt",
            str(STATE / "receipt.json"),
        ]
    )
    print(report)
    env = {
        **os.environ,
        "ONTOBRIDGE_API_URL": url,
        "ONTOBRIDGE_MCP_URL": "http://127.0.0.1:18790/mcp",
        "ONTOBRIDGE_MOCK_CREDENTIAL_FILE": str(authfile),
    }
    print(run(["node", "gateway/pilot-check.mjs"], env=env))
    # A clean restart must retain published capabilities, snapshots and pipeline checkpoints.
    run([*compose, "restart", "worker", "console", "mcp"])
    deadline = time.monotonic() + 45
    token = json.loads(authfile.read_text())["viewer"]
    while True:
        try:
            response = httpx.post(
                url + "/api/v1/invoke/get_order_360",
                headers={"Authorization": "Bearer " + token},
                json={"order_id": "SO-2026-0088"},
                timeout=3,
            )
            if response.status_code == 200:
                assert response.json()["result"]["progress"]["finished"] == 864
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("Persisted API did not recover after restart")
        time.sleep(0.5)
    browser_check = "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); page=b.new_page(); page.set_content('<h1>container browser</h1>'); assert page.locator('h1').inner_text()=='container browser'; b.close(); p.stop(); print('Non-root Chromium runtime passed')"
    print(run([*compose, "exec", "-T", "worker", "/app/.venv/bin/python", "-c", browser_check]))
    print(
        "PASS: private PostgreSQL persistence, independent Worker, official MCP eras, restart recovery and non-root RPA runtime"
    )


if __name__ == "__main__":
    main()
