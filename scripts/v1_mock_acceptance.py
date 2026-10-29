"""Prepare a mock pilot via public APIs while the independent gateway worker runs.
Usage: uv run python scripts/v1_mock_acceptance.py --url http://127.0.0.1:8788
Operates on demo mode, or an explicitly supplied synthetic fixture manifest and role credential file.
Does not bypass approval identities or source/publish APIs.
"""

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8788")
    parser.add_argument("--receipt", default="var/v1-console/acceptance.json")
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--fixture-manifest", type=Path)
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=45) as client:
        if args.credentials or args.fixture_manifest:
            assert args.credentials and args.fixture_manifest, (
                "Both synthetic manifest and credential file are required"
            )
            manifest = json.loads(args.fixture_manifest.read_text())
            assert manifest.get("synthetic_fixture") == "ontobridge-manufacturing-v1"
            tokens = json.loads(args.credentials.read_text())
        else:
            assert client.get("/api/config").json()["demo"], "Use a dedicated mock fixture environment"
            manifest = None
            tokens = {
                role: client.post("/api/demo-session/" + role).json()["token"]
                for role in ["operator", "approver", "viewer"]
            }

        def api(method, path, body=None, role="operator"):
            response = client.request(
                method, "/api" + path, json=body, headers={"Authorization": "Bearer " + tokens[role]}
            )
            if response.status_code >= 400:
                raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:500]}")
            return response.json()

        old_sources, old_mappings = (
            (manifest["sources"], manifest["mappings"])
            if manifest
            else (api("GET", "/sources"), api("GET", "/mappings"))
        )
        sources = api("GET", "/v1/sources")
        pipeline_list = api("GET", "/v1/pipelines")
        source_ids = {}
        for source in old_sources:
            path = source["path"].removeprefix("demo/")
            current = next((s for s in sources if s["path"] == path and s["kind"] == source["kind"]), None)
            if not current:
                current = api(
                    "POST", "/v1/sources", {k: source[k] for k in ["name", "kind", "system"]} | {"path": path}
                )
            api("POST", f"/v1/sources/{current['id']}/test", {})
            api("POST", f"/v1/sources/{current['id']}/discover", {})
            source_ids[source["id"]] = current["id"]
        spec = api("GET", "/v1/ontology-template")
        spec["name"] = "V1 Mock Factory · ERP / MES / WMS"
        ontology = api("POST", "/v1/ontologies", spec)
        for mapping in old_mappings:
            source_id = source_ids[mapping["source_id"]]
            pipeline = next(
                (p for p in pipeline_list if p["source_id"] == source_id and p["table"] == mapping["table"]),
                None,
            )
            if not pipeline:
                pipeline = api(
                    "POST",
                    "/v1/pipelines",
                    {
                        "name": mapping["entity"],
                        "source_id": source_id,
                        "table": mapping["table"],
                        "primary_key": mapping["fields"]["id"],
                    },
                )
            run = api("POST", f"/v1/pipelines/{pipeline['id']}/runs", {})
            deadline = time.monotonic() + 60
            while run["status"] not in ("succeeded", "failed") and time.monotonic() < deadline:
                time.sleep(0.3)
                run = api("GET", f"/v1/runs/{run['id']}")
            assert run["status"] == "succeeded", "Independent worker must be running: " + str(
                run.get("error")
            )
            api(
                "POST",
                "/v1/mappings/suggestions",
                {"ontology_id": ontology["id"], "object": mapping["entity"], "pipeline_id": pipeline["id"]},
            )
            attrs = next(o for o in spec["objects"] if o["name"] == mapping["entity"])["attributes"]
            fields = {
                a["name"]: {"source": mapping["fields"][a["name"]], "transform": a["type"]} for a in attrs
            }
            confirmed = api(
                "POST",
                "/v1/mappings",
                {
                    "ontology_id": ontology["id"],
                    "object": mapping["entity"],
                    "pipeline_id": pipeline["id"],
                    "fields": fields,
                    "explanation": "合成试点：逐项核对已知 fixture 字段，使用独立 PMC 演示身份审批。",
                },
            )
            api("POST", f"/v1/mappings/{confirmed['id']}/approve", {}, role="approver")
            spec["mapping_ids"].append(confirmed["id"])
        api("POST", f"/v1/ontologies/{ontology['id']}/draft", {"revision": 1, "spec": spec})
        quality = api("POST", f"/v1/ontologies/{ontology['id']}/validate", {})
        assert quality["valid"], quality["issues"]
        version = api("POST", f"/v1/ontologies/{ontology['id']}/publish", {"revision": 2}, role="approver")
        templates = {
            "get_order_360": "order_360",
            "check_delivery_risk": "delivery_risk",
            "get_material_shortage": "material_shortage",
            "get_production_progress": "production_progress",
            "list_due_orders": "list_due_orders",
            "propose_schedule_change": "schedule_draft",
            "read_order": "entity_lookup",
        }
        for name, template in templates.items():
            api(
                "POST",
                "/v1/capabilities",
                {
                    "name": name,
                    "title": name.replace("_", " "),
                    "description": "Mock 制造业验收：经过发布本体和沙箱测试的 " + template,
                    "ontology_id": ontology["id"],
                    "ontology_version": version["version"],
                    "template": template,
                    "roles": ["operator", "approver"]
                    if template == "schedule_draft"
                    else ["viewer", "operator", "approver", "admin"],
                },
            )
            api("POST", f"/v1/capabilities/{name}/generate", {})
            report = api("POST", f"/v1/capabilities/{name}/test", {})
            assert report["passed"], report
            api("POST", f"/v1/capabilities/{name}/publish", {}, role="approver")
        result = api("POST", "/v1/invoke/get_order_360", {"order_id": "SO-2026-0088"}, role="viewer")
        assert (
            result["result"]["progress"]["quantity"] == 1200
            and len(result["result"]["progress"]["work_orders"]) == 2
        )
        assert result["result"]["progress"]["finished"] == 864
        receipt = {
            "mock": True,
            "ontology_id": ontology["id"],
            "ontology_version": version["version"],
            "quality": quality["counts"],
            "capabilities": list(templates),
            "order_id": "SO-2026-0088",
            "quantity": 1200,
            "finished": 864,
            "api_url": args.url,
        }
        path = Path(args.receipt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
