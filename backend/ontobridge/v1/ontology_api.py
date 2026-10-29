from uuid import uuid4

from fastapi import Depends

from ..models import ENTITIES, LABELS, RELATIONS, Strict
from ..service import DomainError, required
from ..store import now
from .mapping_assistant import suggest
from .ontology_contracts import (
    IdentityDecision,
    MappingSpec,
    OntologySpec,
    ReasonRequest,
    RevisionRequest,
    RollbackRequest,
    SuggestionRequest,
)
from .ontology_engine import build_snapshot, digest, mapping_dependencies


class OntologyEdit(Strict):
    revision: int
    spec: OntologySpec


def manufacturing_template():
    objects = []
    for i, (name, model) in enumerate(ENTITIES.items()):
        schema = model.model_json_schema()
        attributes = []
        for key, attr in schema["properties"].items():
            kind = "date" if attr.get("format") == "date" else attr["type"]
            attributes.append(
                {
                    "name": key,
                    "label": key,
                    "type": kind,
                    "required": key in schema.get("required", []),
                    "minimum": attr.get("minimum"),
                    "exclusive_minimum": attr.get("exclusiveMinimum"),
                    "maximum": attr.get("maximum"),
                }
            )
        objects.append(
            {
                "name": name,
                "label": LABELS[name],
                "identity": "id",
                "attributes": attributes,
                "constraints": [
                    {
                        "left": "finished",
                        "operator": "lte",
                        "right": "quantity",
                        "message": "完工数量不能超过工单数量",
                    }
                ]
                if name == "WorkOrder"
                else [],
                "position": {"x": (i % 3) * 280, "y": (i // 3) * 360},
            }
        )
    relations = [
        {
            "name": f"{source}_{field}",
            "label": label,
            "from_object": source,
            "to_object": target,
            "from_field": field,
            "to_field": "id",
            "cardinality": "many_to_one",
            "evidence": "来源单据包含明确引用字段；发布前由业务审批人核对。",
        }
        for source, field, target, label, _ in RELATIONS
    ]
    return OntologySpec.model_validate(
        {
            "name": "制造业 ERP / MES / WMS",
            "objects": objects,
            "relations": relations,
            "metrics": [
                {
                    "name": "production_completion",
                    "object": "WorkOrder",
                    "operation": "ratio",
                    "numerator": "finished",
                    "denominator": "quantity",
                    "unit": "ratio",
                    "description": "已完工数量 / 工单计划数量",
                }
            ],
        }
    ).model_dump()


def install(app, store, auth, permit):
    def saved_mappings(tx, ontology):
        return [m for m in tx.all("v1_mapping") if m["ontology_id"] == ontology["id"]]

    def approved_identities(tx, ontology_id, mapping_ids=None):
        return [
            i
            for i in tx.all("v1_identity_decision")
            if i["ontology_id"] == ontology_id
            and i["status"] == "approved"
            and (mapping_ids is None or i["mapping_id"] in mapping_ids)
        ]

    @app.get("/api/v1/ontology-template")
    def template(p=Depends(auth)):
        permit(p, "operator", "approver")
        return manufacturing_template()

    @app.get("/api/v1/ontologies")
    def ontologies(p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            return tx.all("v1_ontology")

    @app.post("/api/v1/ontologies", status_code=201)
    def create(body: OntologySpec, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            result = {
                "id": "ont-" + uuid4().hex,
                "spec": body.model_dump(),
                "revision": 1,
                "created_by": p["user"],
                "edited_by": p["user"],
                "created_at": now(),
                "active_version": None,
            }
            tx.put("v1_ontology", result["id"], result)
            tx.audit(p["user"], "ontology.created", result["id"])
            return result

    @app.post("/api/v1/ontologies/{ontology_id}/draft")
    def edit(ontology_id: str, body: OntologyEdit, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            value = required(tx, "v1_ontology", ontology_id)
            if value["revision"] != body.revision:
                raise DomainError("草案已被修改，请刷新后重试", 409)
            value.update(
                spec=body.spec.model_dump(), revision=body.revision + 1, edited_by=p["user"], edited_at=now()
            )
            tx.put("v1_ontology", ontology_id, value)
            tx.audit(p["user"], "ontology.draft.edited", ontology_id, {"revision": value["revision"]})
            return value

    @app.get("/api/v1/mappings")
    def mappings(p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            return tx.all("v1_mapping")

    @app.post("/api/v1/mappings/suggestions", status_code=201)
    def suggestions(body: SuggestionRequest, p=Depends(auth)):
        permit(p, "operator")
        # Remote model call cannot hold the database transaction/worker lock.
        with store.transaction(p["tenant"]) as tx:
            ontology = required(tx, "v1_ontology", body.ontology_id)
            pipeline = required(tx, "v1_pipeline", body.pipeline_id)
            schema = required(tx, "v1_schema", pipeline["source_id"])

        class Metadata:
            tenant = p["tenant"]

            def get(self, kind, key):
                return {
                    ("v1_pipeline", pipeline["id"]): pipeline,
                    ("v1_schema", pipeline["source_id"]): schema,
                }.get((kind, key))

        result = suggest(Metadata(), body, ontology["spec"], getattr(app.state, "llm_config", None))
        with store.transaction(p["tenant"]) as tx:
            if required(tx, "v1_ontology", body.ontology_id)["revision"] != ontology["revision"]:
                raise DomainError("生成候选时本体已变化，请重试", 409)
            result.update(
                id="map-" + uuid4().hex, version=1, status="draft", created_by=p["user"], created_at=now()
            )
            tx.put("v1_mapping", result["id"], result)
            tx.audit(p["user"], "mapping.suggested", result["id"], {"provider": result["provider"]})
            return result

    @app.post("/api/v1/mappings", status_code=201)
    def mapping_create(body: MappingSpec, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            ontology = required(tx, "v1_ontology", body.ontology_id)
            result = body.model_dump()
            mapping_dependencies(tx, result, ontology["spec"])
            result.update(
                id="map-" + uuid4().hex,
                version=1,
                status="draft",
                created_by=p["user"],
                created_at=now(),
                provider="manual",
            )
            tx.put("v1_mapping", result["id"], result)
            tx.audit(p["user"], "mapping.created", result["id"])
            return result

    @app.post("/api/v1/mappings/{mapping_id}/approve")
    def approve_mapping(mapping_id: str, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            value = required(tx, "v1_mapping", mapping_id)
            if value["created_by"] == p["user"]:
                raise DomainError("候选创建者不能批准自己的映射", 403)
            if value["status"] == "approved":
                return value
            ontology = required(tx, "v1_ontology", value["ontology_id"])
            mapping_dependencies(tx, value, ontology["spec"])
            value.update(status="approved", approved_by=p["user"], approved_at=now())
            tx.put("v1_mapping", mapping_id, value)
            tx.audit(p["user"], "mapping.approved", mapping_id)
            return value

    @app.post("/api/v1/ontologies/{ontology_id}/validate")
    def validate(ontology_id: str, p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            ontology = required(tx, "v1_ontology", ontology_id)
            result = build_snapshot(
                tx,
                ontology_id,
                ontology["spec"],
                saved_mappings(tx, ontology),
                approved_identities(tx, ontology_id, ontology["spec"]["mapping_ids"]),
            )
            result.update(ontology_id=ontology_id, revision=ontology["revision"])
            tx.put("v1_quality", ontology_id, result)
            tx.audit(
                p["user"],
                "ontology.validated",
                ontology_id,
                {"valid": result["valid"], "counts": result["counts"]},
            )
            return result

    @app.post("/api/v1/ontologies/{ontology_id}/publish")
    def publish(ontology_id: str, body: RevisionRequest, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            ontology = required(tx, "v1_ontology", ontology_id)
            if ontology["revision"] != body.revision:
                raise DomainError("草案版本变化，请重新审阅", 409)
            if ontology["edited_by"] == p["user"]:
                raise DomainError("最后编辑者不能发布自己的本体草案", 403)
            mappings = saved_mappings(tx, ontology)
            identities = approved_identities(tx, ontology_id, ontology["spec"]["mapping_ids"])
            snapshot = build_snapshot(tx, ontology_id, ontology["spec"], mappings, identities)
            if not snapshot["valid"]:
                raise DomainError(
                    "本体存在依赖或质量错误，请先运行校验并修正：" + snapshot["issues"][0]["message"], 409
                )
            number = (
                max(
                    [v["version"] for v in tx.all("v1_ontology_version") if v["ontology_id"] == ontology_id]
                    or [0]
                )
                + 1
            )
            key = ontology_id + ":" + str(number)
            result = {
                "id": key,
                "ontology_id": ontology_id,
                "version": number,
                "revision": body.revision,
                "spec": ontology["spec"],
                "mappings": [m for m in mappings if m["id"] in ontology["spec"]["mapping_ids"]],
                "identities": identities,
                "published_by": p["user"],
                "published_at": now(),
                "status": "published",
                "snapshot_id": key + ":initial",
            }
            result["digest"] = digest(
                {"spec": result["spec"], "mappings": result["mappings"], "identities": identities}
            )
            tx.put("v1_ontology_version", key, result)
            tx.put(
                "v1_snapshot",
                result["snapshot_id"],
                {**snapshot, "id": result["snapshot_id"], "ontology_version": key},
            )
            ontology.update(active_version=number, published_revision=body.revision)
            tx.put("v1_ontology", ontology_id, ontology)
            tx.audit(p["user"], "ontology.published", key, {"digest": result["digest"]})
            return result

    @app.get("/api/v1/ontologies/{ontology_id}/versions")
    def versions(ontology_id: str, p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            required(tx, "v1_ontology", ontology_id)
            return [v for v in tx.all("v1_ontology_version") if v["ontology_id"] == ontology_id]

    @app.post("/api/v1/ontologies/{ontology_id}/rollback")
    def rollback(ontology_id: str, body: RollbackRequest, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            ontology = required(tx, "v1_ontology", ontology_id)
            version = required(tx, "v1_ontology_version", ontology_id + ":" + str(body.version))
            if version["status"] != "published":
                raise DomainError("只能回滚到已发布版本", 409)
            previous = ontology["active_version"]
            ontology["active_version"] = body.version
            tx.put("v1_ontology", ontology_id, ontology)
            tx.audit(
                p["user"],
                "ontology.rolled_back",
                ontology_id,
                {"from": previous, "to": body.version, "reason": body.reason},
            )
            return ontology

    @app.post("/api/v1/ontologies/{ontology_id}/refresh")
    def refresh(ontology_id: str, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            ontology = required(tx, "v1_ontology", ontology_id)
            version = required(tx, "v1_ontology_version", ontology_id + ":" + str(ontology["active_version"]))
            snapshot = build_snapshot(
                tx, ontology_id, version["spec"], version["mappings"], version["identities"]
            )
            key = version["id"] + ":" + uuid4().hex[:16]
            tx.put("v1_snapshot", key, {**snapshot, "id": key, "ontology_version": version["id"]})
            version.update(last_refresh_at=now(), refresh_valid=snapshot["valid"], last_quality_id=key)
            if snapshot["valid"]:
                version["snapshot_id"] = key
            tx.put("v1_ontology_version", version["id"], version)
            tx.audit(
                p["user"],
                "ontology.refreshed" if snapshot["valid"] else "ontology.refresh.blocked",
                version["id"],
            )
            return snapshot

    @app.get("/api/v1/ontologies/{ontology_id}/snapshot")
    def snapshot(ontology_id: str, version: int | None = None, p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            ontology = required(tx, "v1_ontology", ontology_id)
            published = required(
                tx, "v1_ontology_version", ontology_id + ":" + str(version or ontology["active_version"])
            )
            return required(tx, "v1_snapshot", published["snapshot_id"])

    @app.get("/api/v1/identities/reviews")
    def identity_reviews(p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            return tx.all("v1_identity_decision")

    @app.post("/api/v1/identities/reviews", status_code=201)
    def identity_create(body: IdentityDecision, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            required(tx, "v1_ontology", body.ontology_id)
            mappings = [required(tx, "v1_mapping", key) for key in [body.mapping_id, body.target_mapping_id]]
            if (
                any(m["ontology_id"] != body.ontology_id or m["status"] != "approved" for m in mappings)
                or mappings[0]["object"] != mappings[1]["object"]
            ):
                raise DomainError("身份合并必须引用同一本体中同类型的批准映射")
            for m, key in zip(mappings, [body.source_key, body.target_source_key], strict=True):
                if not any(
                    a["pipeline_id"] == m["pipeline_id"] and a["source_key"] == key
                    for a in tx.all("v1_asset")
                ):
                    raise DomainError("来源身份不存在")
            result = {
                **body.model_dump(),
                "id": "identity-" + uuid4().hex,
                "status": "draft",
                "created_by": p["user"],
                "created_at": now(),
            }
            tx.put("v1_identity_decision", result["id"], result)
            tx.audit(p["user"], "identity.review.created", result["id"])
            return result

    @app.post("/api/v1/identities/reviews/{review_id}/approve")
    def identity_approve(review_id: str, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            result = required(tx, "v1_identity_decision", review_id)
            if result["status"] == "revoked":
                raise DomainError("已撤销的身份决议不可重新批准，请创建新复核", 409)
            if result["created_by"] == p["user"]:
                raise DomainError("不能批准自己的身份复核", 403)
            for decision in approved_identities(tx, result["ontology_id"]):
                if decision["id"] != review_id and (decision["mapping_id"], decision["source_key"]) == (
                    result["mapping_id"],
                    result["source_key"],
                ):
                    raise DomainError("该来源身份已有批准决议，请修正映射后创建新版本", 409)
            result.update(status="approved", approved_by=p["user"], approved_at=now())
            tx.put("v1_identity_decision", review_id, result)
            tx.audit(p["user"], "identity.review.approved", review_id)
            return result

    @app.post("/api/v1/identities/reviews/{review_id}/revoke")
    def identity_revoke(review_id: str, body: ReasonRequest, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            result = required(tx, "v1_identity_decision", review_id)
            if result["status"] != "approved":
                raise DomainError("仅可撤销已批准的身份决议", 409)
            result.update(status="revoked", revoked_by=p["user"], revoked_at=now(), revoke_reason=body.reason)
            tx.put("v1_identity_decision", review_id, result)
            tx.audit(p["user"], "identity.review.revoked", review_id, {"reason": body.reason})
            # Published versions retain their immutable decision snapshots until a new version is published.
            return result
