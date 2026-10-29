import json
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi.testclient import TestClient
from ontobridge.api import create_app
from ontobridge.v1.ontology_contracts import OntologySpec
from ontobridge.v1.pipelines import GatewayWorker

TOKENS = {
    role: role + "-ontology-test-token-long-enough" for role in ("operator", "approver", "viewer", "admin")
}


def post(client, path, body=None, role="operator", code=200):
    response = client.post("/api/v1" + path, json=body, headers={"Authorization": "Bearer " + TOKENS[role]})
    assert response.status_code == code, response.text
    return response.json()


def get(client, path, role="operator"):
    response = client.get("/api/v1" + path, headers={"Authorization": "Bearer " + TOKENS[role]})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def platform(tmp_path):
    app = create_app(
        tmp_path,
        demo=False,
        credentials={
            token: {"user": role, "tenant": "factory", "role": role} for role, token in TOKENS.items()
        },
    )
    folder = app.state.source_root / "factory"
    folder.mkdir()
    with TestClient(app) as client:
        yield app, client, folder


def ingest(platform, name, content):
    app, client, folder = platform
    (folder / (name + ".csv")).write_text(content)
    source = post(
        client, "/sources", {"name": name, "kind": "csv", "system": "ERP", "path": name + ".csv"}, code=201
    )
    post(client, "/sources/" + source["id"] + "/discover")
    pipeline = post(
        client,
        "/pipelines",
        {"name": name, "source_id": source["id"], "table": name, "primary_key": "pk"},
        code=201,
    )
    sync(app, client, pipeline)
    return pipeline


def sync(app, client, pipeline):
    post(client, "/pipelines/" + pipeline["id"] + "/runs", code=202)
    worker = GatewayWorker(app.state.store, app.state.source_root, app.state.connector_registry)
    for _ in range(5):
        worker.tick("factory")


def spec():
    return {
        "name": "Mock Factory",
        "objects": [
            {
                "name": "SalesOrder",
                "label": "销售订单",
                "identity": "id",
                "attributes": [
                    {"name": "id"},
                    {"name": "customer"},
                    {"name": "quantity", "type": "integer", "minimum": 1},
                    {"name": "status", "enum": ["open", "closed"]},
                    {"name": "weight", "type": "number", "unit": "kg", "minimum": 0},
                ],
            },
            {
                "name": "WorkOrder",
                "label": "生产工单",
                "identity": "id",
                "attributes": [
                    {"name": "id"},
                    {"name": "order_id"},
                    {"name": "quantity", "type": "integer", "minimum": 1},
                    {"name": "finished", "type": "integer", "minimum": 0},
                ],
            },
        ],
        "relations": [
            {
                "name": "order_work",
                "label": "工单归属",
                "from_object": "WorkOrder",
                "to_object": "SalesOrder",
                "from_field": "order_id",
                "to_field": "id",
                "evidence": "MES 工单中的 ERP 销售订单单号引用",
            }
        ],
        "metrics": [
            {
                "name": "completion",
                "object": "WorkOrder",
                "operation": "ratio",
                "numerator": "finished",
                "denominator": "quantity",
                "description": "累计完成率",
            }
        ],
    }


def setup(platform, bad=False):
    app, client, _ = platform
    orders = ingest(platform, "orders", "pk,id,customer,quantity,status,weight\n1,SO-88,Buyer,100,A,2500\n")
    work = ingest(
        platform,
        "work",
        "pk,id,order_id,quantity,finished\n1,MO-1,SO-88,60,30\n2,MO-2,"
        + ("MISSING" if bad else "SO-88")
        + ",40,20\n",
    )
    ontology = post(client, "/ontologies", spec(), code=201)
    mappings = []
    for entity, pipeline in [("SalesOrder", orders), ("WorkOrder", work)]:
        suggestion = post(
            client,
            "/mappings/suggestions",
            {"ontology_id": ontology["id"], "object": entity, "pipeline_id": pipeline["id"]},
            code=201,
        )
        assert suggestion["status"] == "draft" and suggestion["provider"] == "mock"
        if entity == "SalesOrder":
            fields = suggestion["fields"]
            fields["status"] = {
                "source": "status",
                "transform": "enum",
                "values": {"A": "open", "C": "closed"},
            }
            fields["weight"] = {
                "source": "weight",
                "transform": "unit",
                "source_unit": "g",
                "target_unit": "kg",
            }
            suggestion = post(
                client,
                "/mappings",
                {
                    "ontology_id": ontology["id"],
                    "object": entity,
                    "pipeline_id": pipeline["id"],
                    "fields": fields,
                    "explanation": "逐项核对订单引用、状态与克转千克",
                },
                code=201,
            )
        mappings.append(suggestion)
    draft = ontology["spec"]
    draft["mapping_ids"] = [m["id"] for m in mappings]
    ontology = post(client, "/ontologies/" + ontology["id"] + "/draft", {"revision": 1, "spec": draft})
    for mapping in mappings:
        post(client, "/mappings/" + mapping["id"] + "/approve", role="approver")
    return ontology, mappings, orders, work


def test_published_versions_are_immutable_and_evidenced(platform):
    _, client, _ = platform
    ontology, mappings, _, _ = setup(platform)
    path = "/ontologies/" + ontology["id"]
    preview = post(client, path + "/validate")
    assert preview["valid"] and preview["counts"] == {"entities": 3, "aliases": 3, "relationships": 2}
    assert preview["metrics"]["completion"]["value"] == 0.5
    assert next(e for e in preview["entities"] if e["entity_type"] == "SalesOrder")["data"]["weight"] == 2.5
    assert all(r["evidence_ref"][0]["mapping_id"] == mappings[1]["id"] for r in preview["relationships"])
    post(client, path + "/publish", {"revision": 1}, role="approver", code=409)
    first = post(client, path + "/publish", {"revision": 2}, role="approver")
    original = get(client, path + "/snapshot")
    revised = deepcopy(ontology["spec"])
    revised["name"] = "New name"
    post(client, path + "/draft", {"revision": 2, "spec": revised})
    assert get(client, path + "/snapshot") == original
    post(client, path + "/publish", {"revision": 3}, role="approver")
    rolled = post(client, path + "/rollback", {"version": 1, "reason": "恢复已核对版本"}, role="approver")
    assert rolled["active_version"] == 1
    assert get(client, path + "/versions")[0]["digest"] == first["digest"]
    assert get(client, path + "/snapshot")["entities"] == original["entities"]


def test_orphan_and_bad_refresh_preserve_last_good_snapshot(platform):
    app, client, folder = platform
    ontology, _, _, work = setup(platform, bad=True)
    path = "/ontologies/" + ontology["id"]
    check = post(client, path + "/validate")
    assert not check["valid"] and "orphan_relation" in [i["code"] for i in check["issues"]]
    post(client, path + "/publish", {"revision": 2}, role="approver", code=409)
    file = folder / "work.csv"
    file.write_text(file.read_text().replace("MISSING", "SO-88"))
    sync(app, client, work)
    post(client, path + "/publish", {"revision": 2}, role="approver")
    original = get(client, path + "/snapshot")
    file.write_text(file.read_text().replace("MO-2,SO-88", "MO-2,MISSING"))
    sync(app, client, work)
    assert not post(client, path + "/refresh")["valid"]
    assert get(client, path + "/snapshot") == original
    assert get(client, path + "/versions")[0]["refresh_valid"] is False


def test_identity_conflict_requires_independent_approval(platform):
    _, client, _ = platform
    ontology, mappings, _, _ = setup(platform)
    other = ingest(platform, "other", "pk,id,customer,quantity,status,weight\n9,SO-88,Buyer,100,A,2500\n")
    mapping = post(
        client,
        "/mappings",
        {
            **{k: mappings[0][k] for k in ["ontology_id", "object", "fields", "explanation"]},
            "pipeline_id": other["id"],
        },
        code=201,
    )
    post(client, "/mappings/" + mapping["id"] + "/approve", role="approver")
    ontology["spec"]["mapping_ids"].append(mapping["id"])
    path = "/ontologies/" + ontology["id"]
    post(client, path + "/draft", {"revision": 2, "spec": ontology["spec"]})
    check = post(client, path + "/validate")
    assert {"identity_conflict", "ambiguous_relation"} <= {i["code"] for i in check["issues"]}
    review = post(
        client,
        "/identities/reviews",
        {
            "ontology_id": ontology["id"],
            "mapping_id": mapping["id"],
            "source_key": "9",
            "target_mapping_id": mappings[0]["id"],
            "target_source_key": "1",
            "reason": "业务已核对两套系统单据，ERP 为权威来源",
        },
        code=201,
    )
    post(client, "/identities/reviews/" + review["id"] + "/approve", code=403)
    post(client, "/identities/reviews/" + review["id"] + "/approve", role="approver")
    check = post(client, path + "/validate")
    assert check["valid"] and check["counts"]["entities"] == 3 and check["counts"]["aliases"] == 4
    aliases = [a for a in check["aliases"] if a["entity_type"] == "SalesOrder"]
    assert aliases[0]["canonical_id"] == aliases[1]["canonical_id"]
    post(client, path + "/publish", {"revision": 3}, role="approver")
    original = get(client, path + "/snapshot")
    endpoint = "/identities/reviews/" + review["id"]
    post(client, endpoint + "/revoke", {"reason": "核对后撤销，重新检查单据身份"}, code=403)
    post(client, endpoint + "/revoke", {"reason": "核对后撤销，重新检查单据身份"}, role="approver")
    post(client, endpoint + "/approve", role="approver", code=409)
    assert not post(client, path + "/validate")["valid"]
    assert get(client, path + "/snapshot") == original
    assert post(client, path + "/refresh")["valid"]  # Published decisions remain immutable.
    assert get(client, path + "/snapshot")["entities"] == original["entities"]


def test_duplicate_identity_enum_unit_and_type_constraints(platform):
    app, client, folder = platform
    ontology, _, orders, _ = setup(platform)
    file = folder / "orders.csv"
    path = "/ontologies/" + ontology["id"] + "/validate"
    for content, code in [
        ("1,SO-88,Buyer,100,X,2500\n", "attribute_constraint"),
        ("1,SO-88,Buyer,1.5,A,2500\n", "attribute_constraint"),
        ("1,SO-88,Buyer,100,A,2500\n2,SO-88,Other,100,A,2500\n", "duplicate_identity"),
    ]:
        file.write_text("pk,id,customer,quantity,status,weight\n" + content)
        sync(app, client, orders)
        result = post(client, path)
        assert not result["valid"] and code in {i["code"] for i in result["issues"]}


def test_review_gates_and_schema_reject_executable_code(platform):
    _, client, _ = platform
    ontology, mappings, _, _ = setup(platform)
    post(client, "/ontologies/" + ontology["id"] + "/publish", {"revision": 2}, role="viewer", code=403)
    bad = spec()
    bad["metrics"][0]["operation"] = "eval"
    post(client, "/ontologies", bad, code=422)
    bad = spec()
    bad["relations"][0]["to_field"] = "missing"
    post(client, "/ontologies", bad, code=422)
    bad = {
        "ontology_id": ontology["id"],
        "pipeline_id": mappings[0]["pipeline_id"],
        "object": "SalesOrder",
        "fields": {},
        "explanation": "test",
    }
    post(client, "/mappings", bad, code=422)
    # A user with admin role is still subject to independent approval.
    draft = post(client, "/ontologies", spec(), role="admin", code=201)
    post(client, "/ontologies/" + draft["id"] + "/publish", {"revision": 1}, role="admin", code=403)


def test_configured_llm_structured_response_and_invented_field_rejection(platform):
    app, client, _ = platform
    pipeline = ingest(platform, "model", "pk,id,customer,quantity,status,weight\n1,SO-88,Buyer,100,A,2500\n")
    ontology = post(client, "/ontologies", spec(), code=201)
    received = []
    output = {
        "fields": {"id": {"source": "id", "transform": "string"}},
        "explanation": "Model fixture returns a partial proposal for review.",
        "confidence": 0.7,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": json.dumps(output)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app.state.llm_config = {
        "provider": "openai_compatible",
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "api_key": "local-fixture",
        "model": "mock-model",
    }
    body = {"ontology_id": ontology["id"], "pipeline_id": pipeline["id"], "object": "SalesOrder"}
    try:
        result = post(client, "/mappings/suggestions", body, code=201)
        assert result["provider"] == "openai_compatible" and not result["complete"]
        assert "SO-88" not in json.dumps(received) and "Buyer" not in json.dumps(received)
        assert received[0]["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        output["fields"]["id"]["source"] = "invented_field"
        post(client, "/mappings/suggestions", body, code=422)
        assert len(get(client, "/mappings")) == 1
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_many_to_many_requires_real_junction_and_type_match():
    value = spec()
    value["relations"][0]["cardinality"] = "many_to_many"
    with pytest.raises(ValueError, match="关联单据"):
        OntologySpec.model_validate(value)
    value = spec()
    value["objects"][1]["attributes"][1]["type"] = "integer"
    with pytest.raises(ValueError, match="类型"):
        OntologySpec.model_validate(value)


def test_cross_field_constraints_block_invalid_business_facts(platform):
    app, client, folder = platform
    ontology, _, _, work = setup(platform)
    path = "/ontologies/" + ontology["id"]
    ontology["spec"]["objects"][1]["constraints"] = [
        {"left": "finished", "operator": "lte", "right": "quantity", "message": "完工数不能大于计划数"}
    ]
    post(client, path + "/draft", {"revision": 2, "spec": ontology["spec"]})
    file = folder / "work.csv"
    file.write_text(file.read_text().replace("60,30", "60,61"))
    sync(app, client, work)
    result = post(client, path + "/validate")
    assert not result["valid"] and any(i["message"] == "完工数不能大于计划数" for i in result["issues"])
    post(client, path + "/publish", {"revision": 3}, role="approver", code=409)


def test_junction_evidence_and_null_references(platform):
    _, client, _ = platform
    pipelines = {
        "Part": ingest(platform, "parts", "pk,id,code\n1,P1,\n2,P2,C2\n"),
        "Batch": ingest(platform, "batches", "pk,id,code\n1,B1,\n2,B2,C2\n"),
        "Allocation": ingest(
            platform, "allocations", "pk,id,part,batch\n1,A1,P1,B1\n2,A2,P2,B1\n3,A3,P1,B2\n"
        ),
    }
    value = {
        "name": "Evidence graph",
        "objects": [
            {
                "name": name,
                "label": name,
                "identity": "id",
                "attributes": [{"name": "id"}] + [{"name": f, "required": f != "code"} for f in fields],
            }
            for name, fields in [("Part", ["code"]), ("Batch", ["code"]), ("Allocation", ["part", "batch"])]
        ],
        "relations": [
            {
                "name": "allocation",
                "label": "Allocation",
                "from_object": "Part",
                "to_object": "Batch",
                "from_field": "id",
                "to_field": "id",
                "cardinality": "many_to_many",
                "via_object": "Allocation",
                "via_from_field": "part",
                "via_to_field": "batch",
                "evidence": "Actual allocation documents",
            },
            {
                "name": "code_match",
                "label": "Code",
                "from_object": "Part",
                "to_object": "Batch",
                "from_field": "code",
                "to_field": "code",
                "required": False,
                "evidence": "Explicit optional code relationship",
            },
        ],
        "mapping_ids": [],
    }
    ontology = post(client, "/ontologies", value, code=201)
    for obj in value["objects"]:
        mapping = post(
            client,
            "/mappings",
            {
                "ontology_id": ontology["id"],
                "object": obj["name"],
                "pipeline_id": pipelines[obj["name"]]["id"],
                "fields": {
                    a["name"]: {"source": a["name"], "transform": "string"} for a in obj["attributes"]
                },
                "explanation": "Explicit fixture mapping",
            },
            code=201,
        )
        post(client, "/mappings/" + mapping["id"] + "/approve", role="approver")
        value["mapping_ids"].append(mapping["id"])
    path = "/ontologies/" + ontology["id"]
    post(client, path + "/draft", {"revision": 1, "spec": value})
    result = post(client, path + "/validate")
    assert result["valid"] and result["counts"]["relationships"] == 4
    links = [r for r in result["relationships"] if r["relation_type"] == "allocation"]
    assert len(links) == 3 and all(
        r["evidence_ref"][0]["pipeline_id"] == pipelines["Allocation"]["id"] for r in links
    )
    # Two null values never create a relationship; only C2 has a code match.
    assert len([r for r in result["relationships"] if r["relation_type"] == "code_match"]) == 1
