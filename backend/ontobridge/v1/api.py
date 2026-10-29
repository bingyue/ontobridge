from uuid import uuid4

from fastapi import Depends

from ..models import Strict
from ..service import DomainError, required
from ..store import now
from .connector_sdk import ConnectorContext
from .contracts import PipelineSpec, SourceSpec
from .pipelines import enqueue
from .registry import create_registry
from .rpa import WorkflowSpec, prepare_source


class RunRequest(Strict):
    force_full: bool = False


def install(app, store, root, auth, permit):
    from .capability_api import install as install_capabilities
    from .ontology_api import install as install_ontology

    install_ontology(app, store, auth, permit)
    install_capabilities(app, store, auth, permit)
    registry = create_registry()
    app.state.connector_registry = registry

    def connector(source, principal, transaction=None):
        if transaction is not None:
            source = prepare_source(transaction, source)
        else:
            with store.transaction(principal["tenant"]) as tx:
                source = prepare_source(tx, source)
        return registry.create(
            ConnectorContext(
                root, principal["tenant"], source, getattr(app.state, "connector_profiles", None)
            )
        )

    @app.get("/api/v1/connectors")
    def connectors(p=Depends(auth)):
        permit(p, "operator")
        return registry.manifest()

    @app.get("/api/v1/sources")
    def sources(p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            return tx.all("v1_source")

    @app.post("/api/v1/sources", status_code=201)
    def source_create(body: SourceSpec, p=Depends(auth)):
        permit(p, "operator")
        if body.kind not in {item["kind"] for item in registry.manifest()}:
            raise DomainError("连接器插件尚未安装，请先由管理员部署", 422)
        source = {
            "id": "src-" + uuid4().hex[:12],
            **body.model_dump(),
            "status": "configured",
            "version": 1,
            "created_at": now(),
        }
        # Saving a definition does not contact a network source; test/discover are explicit operations.
        with store.transaction(p["tenant"]) as tx:
            tx.put("v1_source", source["id"], source)
            tx.audit(p["user"], "source.v1.created", source["id"], {"kind": source["kind"]})
        return source

    @app.post("/api/v1/sources/{source_id}/test")
    def source_test(source_id: str, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            source = required(tx, "v1_source", source_id)
        try:
            result = connector(source, p).test()
        except DomainError as exc:
            with store.transaction(p["tenant"]) as tx:
                tx.audit(p["user"], "source.test.failed", source_id, {"error": exc.message})
            raise
        with store.transaction(p["tenant"]) as tx:
            tx.audit(p["user"], "source.test.passed", source_id)
        return result

    @app.post("/api/v1/sources/{source_id}/discover")
    def source_discover(source_id: str, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            source = required(tx, "v1_source", source_id)
        result = [t.model_dump(mode="json") for t in connector(source, p).discover()]
        with store.transaction(p["tenant"]) as tx:
            tx.put("v1_schema", source_id, {"source_id": source_id, "tables": result, "discovered_at": now()})
            tx.audit(p["user"], "source.discovered", source_id, {"tables": len(result)})
        return result

    @app.get("/api/v1/pipelines")
    def pipelines(p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            return tx.all("v1_pipeline")

    @app.post("/api/v1/pipelines", status_code=201)
    def pipeline_create(body: PipelineSpec, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            source = required(tx, "v1_source", body.source_id)
            if body.mode == "incremental" and not connector(source, p, tx).incremental:
                raise DomainError("该来源不支持增量游标，请使用全量快照")
            schema = required(tx, "v1_schema", source["id"])
            table = next((t for t in schema["tables"] if t["name"] == body.table), None)
            if not table:
                raise DomainError("请先发现该来源表")
            columns = {c["name"] for c in table["columns"]}
            if body.primary_key not in columns or (body.cursor and body.cursor.column not in columns):
                raise DomainError("主键或增量列不存在")
            if any(
                old["source_id"] == body.source_id and old["table"] == body.table
                for old in tx.all("v1_pipeline")
            ):
                raise DomainError("该来源表已存在流水线，禁止重复创建互相冲突的数据资产", 409)
            result = {
                "id": "pipe-" + uuid4().hex[:12],
                **body.model_dump(),
                "version": 1,
                "checkpoint": None,
                "created_at": now(),
            }
            tx.put("v1_pipeline", result["id"], result)
            tx.audit(p["user"], "pipeline.created", result["id"])
            return result

    @app.post("/api/v1/pipelines/{pipeline_id}/runs", status_code=202)
    def pipeline_run(pipeline_id: str, body: RunRequest | None = None, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            return enqueue(tx, pipeline_id, p["user"], body.force_full if body else False)

    @app.get("/api/v1/runs")
    def runs(p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            return sorted(tx.all("v1_run"), key=lambda r: r["created_at"], reverse=True)[:100]

    @app.get("/api/v1/runs/{run_id}")
    def run_detail(run_id: str, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            return required(tx, "v1_run", run_id)

    @app.get("/api/v1/assets")
    def assets(pipeline_id: str | None = None, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            rows = [a for a in tx.all("v1_asset") if pipeline_id is None or a["pipeline_id"] == pipeline_id]
            return {"count": len(rows), "items": rows[:1000], "truncated": len(rows) > 1000}

    @app.get("/api/v1/operations")
    def operations(p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            return {
                "workers": tx.all("v1_worker"),
                "alerts": tx.all("v1_alert"),
                "runs": sorted(tx.all("v1_run"), key=lambda r: r["created_at"], reverse=True)[:100],
                "audit": sorted(tx.all("audit"), key=lambda r: r["at"], reverse=True)[:200],
            }

    @app.get("/api/v1/rpa/workflows")
    def rpa_workflows(p=Depends(auth)):
        permit(p, "operator", "approver")
        with store.transaction(p["tenant"]) as tx:
            return tx.all("v1_rpa_workflow")

    @app.post("/api/v1/rpa/workflows", status_code=201)
    def rpa_create(body: WorkflowSpec, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            value = {
                "id": "rpa-" + uuid4().hex,
                **body.model_dump(),
                "status": "draft",
                "created_by": p["user"],
                "created_at": now(),
            }
            tx.put("v1_rpa_workflow", value["id"], value)
            tx.audit(p["user"], "rpa.recording.saved", value["id"])
            return value

    @app.post("/api/v1/rpa/workflows/{workflow_id}/approve")
    def rpa_approve(workflow_id: str, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            workflow = required(tx, "v1_rpa_workflow", workflow_id)
            if workflow["created_by"] == p["user"]:
                raise DomainError("录制者不能批准自己的导出流程", 403)
            workflow.update(status="approved", approved_by=p["user"], approved_at=now())
            tx.put("v1_rpa_workflow", workflow_id, workflow)
            tx.audit(p["user"], "rpa.approved", workflow_id)
            return workflow
