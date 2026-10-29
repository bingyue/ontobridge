import json
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .connectors import discover
from .demo import seed
from .models import (
    ENTITIES,
    LABELS,
    RELATIONS,
    CapabilityArgs,
    Decision,
    MappingInput,
    Resolution,
    SourceInput,
)
from .service import (
    CAPABILITIES,
    Business,
    DomainError,
    call_capability,
    preview_mapping,
    required,
    sync_source,
)
from .store import Store, now


def create_app(data_dir=None, demo=None, credentials=None, db_url=None):
    data_dir = Path(data_dir or os.environ.get("ONTOBRIDGE_DATA", "var")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    root = Path(os.environ.get("ONTOBRIDGE_SOURCE_ROOT", str(data_dir / "sources"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    demo = os.environ.get("ONTOBRIDGE_DEMO", "1") == "1" if demo is None else demo
    credentials = (
        credentials if credentials is not None else json.loads(os.environ.get("ONTOBRIDGE_TOKENS", "{}"))
    )
    if not demo and not credentials:
        raise RuntimeError("非演示模式必须配置 ONTOBRIDGE_TOKENS")
    for token, principal in credentials.items():
        if (
            len(token) < 24
            or principal.get("role") not in ("viewer", "operator", "approver", "admin")
            or not principal.get("tenant")
            or not principal.get("user")
        ):
            raise RuntimeError("令牌至少 24 位，且必须包含 user、tenant、role")
    from .v1.access import DataPolicy, restricted_path_allowed

    for principal in credentials.values():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", principal["tenant"]):
            raise RuntimeError("租户标识只允许字母、数字、连字符和下划线")
        if "data_policy" in principal:
            principal["data_policy"] = DataPolicy.model_validate(principal["data_policy"]).model_dump()
    if demo and credentials:
        raise RuntimeError("演示模式不接受真实凭据，请关闭 ONTOBRIDGE_DEMO")
    store = Store(
        db_url or os.environ.get("ONTOBRIDGE_DATABASE_URL", f"sqlite:///{data_dir / 'ontobridge.sqlite'}")
    )
    if demo:
        seed(store, root)
    demo_profiles = [
        {"user": "fde.demo", "role": "operator", "label": "FDE 工程师", "tenant": "demo"},
        {"user": "pmc.demo", "role": "approver", "label": "PMC 审批人", "tenant": "demo"},
        {"user": "viewer.demo", "role": "viewer", "label": "只读访客", "tenant": "demo"},
    ]
    demo_tokens = {p["role"]: secrets.token_urlsafe(32) for p in demo_profiles}
    token_map = {**credentials, **({demo_tokens[p["role"]]: p for p in demo_profiles} if demo else {})}

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.engine.dispose()

    app = FastAPI(
        title="OntoBridge API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.store, app.state.source_root = store, root

    @app.middleware("http")
    async def local_guard(request, call_next):
        if demo and request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            return JSONResponse({"detail": "演示模式只允许本机访问"}, status_code=403)
        # Bind browser API access to the same origin; prevents drive-by localhost requests.
        if request.url.path.startswith("/api"):
            origin = request.headers.get("origin")
            allowed = {f"http://{request.headers.get('host')}", f"https://{request.headers.get('host')}"}
            if demo:
                allowed.add("http://127.0.0.1:5173")
            if origin and origin not in allowed:
                return JSONResponse({"detail": "不允许跨来源请求"}, status_code=403)
            host = request.url.hostname
            if demo and host not in ("127.0.0.1", "localhost", "::1", "testserver"):
                return JSONResponse({"detail": "不允许的演示主机名"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_handler(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    async def auth(request: Request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        principal = next((p for key, p in token_map.items() if secrets.compare_digest(key, token)), None)
        if not principal:
            raise HTTPException(401, "需要有效访问令牌")
        restricted = principal.get("data_policy") is not None
        legacy = (
            request.url.path.startswith("/api/")
            and not request.url.path.startswith("/api/v1/")
            and request.url.path not in ("/api/me",)
        )
        if (restricted and not restricted_path_allowed(request.url.path, request.method)) or (
            not demo and legacy
        ):
            with store.transaction(principal["tenant"]) as tx:
                tx.audit(principal["user"], "access.denied", "data_policy", {"path": request.url.path})
            raise DomainError("当前凭据仅允许通过已发布能力访问授权数据", 403)
        return principal

    def permit(principal, *roles):
        if principal["role"] not in roles and principal["role"] != "admin":
            with store.transaction(principal["tenant"]) as tx:
                tx.audit(principal["user"], "access.denied", "role", {"role": principal["role"]})
            raise DomainError("当前角色无权执行此操作", 403)

    def safe_read(callback):
        try:
            return callback()
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise DomainError("来源读取或数据校验失败：" + str(exc)[:300]) from exc

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/config")
    def config():
        return {"demo": demo, "profiles": demo_profiles if demo else [], "writeback_enabled": False}

    @app.post("/api/demo-session/{role}")
    def demo_session(role: str):
        if not demo or role not in demo_tokens:
            raise DomainError("演示登录不可用", 404)
        return {"token": demo_tokens[role], "principal": next(p for p in demo_profiles if p["role"] == role)}

    @app.get("/api/me")
    def me(p=Depends(auth)):
        return {
            **p,
            "legacy_access": demo and p.get("data_policy") is None,
            "capability_only": p.get("data_policy") is not None,
        }

    @app.get("/api/overview")
    def overview(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            business = Business(tx)
            orders = [
                {**o["data"], "canonical_id": o["id"], "risk": business.risk(o)}
                for o in business.rows("SalesOrder")
            ]
            return {
                "orders": orders,
                "object_count": len(business.objects),
                "source_count": len(tx.all("source")),
                "entity_count": len({o["entity"] for o in business.objects}),
                "relation_count": sum(
                    bool(business.relation(o, field, target))
                    for entity, field, target, _, _ in RELATIONS
                    for o in business.rows(entity)
                ),
                "issues": business.issues(),
                "warnings": business.warnings(),
                "pending_actions": sum(a["status"] == "pending_approval" for a in tx.all("action")),
                "runs": sorted(tx.all("run"), key=lambda r: r["at"], reverse=True)[:5],
            }

    @app.get("/api/sources")
    def sources(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            return tx.all("source")

    @app.post("/api/sources", status_code=201)
    def create_source(body: SourceInput, p=Depends(auth)):
        permit(p, "operator")
        source = {
            "id": "src-" + uuid4().hex[:8],
            **body.model_dump(),
            "status": "configured",
            "created_at": now(),
            "demo": False,
        }
        safe_read(lambda: discover(root, source))
        with store.transaction(p["tenant"]) as tx:
            tx.put("source", source["id"], source)
            tx.audit(p["user"], "source.created", source["id"])
        return source

    @app.get("/api/sources/{source_id}/schema")
    def source_schema(source_id: str, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            source = required(tx, "source", source_id)
            return safe_read(lambda: discover(root, source))

    @app.post("/api/sources/{source_id}/sync")
    def sync(source_id: str, p=Depends(auth)):
        permit(p, "operator")
        try:
            with store.transaction(p["tenant"]) as tx:
                return safe_read(lambda: sync_source(tx, root, source_id, p["user"]))
        except DomainError as exc:
            with store.transaction(p["tenant"]) as tx:
                source = tx.get("source", source_id)
                if source:
                    source.update(status="error", last_error=exc.message)
                    tx.put("source", source_id, source)
                    rid = now() + uuid4().hex
                    tx.put(
                        "run",
                        rid,
                        {
                            "id": rid,
                            "at": now(),
                            "source_id": source_id,
                            "source_name": source["name"],
                            "status": "failed",
                            "error": exc.message,
                            "records": 0,
                        },
                    )
                    tx.audit(p["user"], "sync.failed", source_id, {"error": exc.message})
            raise

    @app.get("/api/mappings")
    def mappings(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            return tx.all("mapping")

    @app.post("/api/mappings/preview")
    def preview(body: MappingInput, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            rows, errors = safe_read(lambda: preview_mapping(tx, root, body.model_dump()))
            return {
                "rows": rows[:5],
                "valid_count": len(rows),
                "errors": errors[:20],
                "error_count": len(errors),
            }

    @app.post("/api/mappings", status_code=201)
    def save_mapping(body: MappingInput, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            safe_read(lambda: preview_mapping(tx, root, body.model_dump()))
            old = next(
                (
                    m
                    for m in tx.all("mapping")
                    if m["source_id"] == body.source_id
                    and m["table"] == body.table
                    and m["entity"] == body.entity
                ),
                None,
            )
            mid = old["id"] if old else "map-" + uuid4().hex[:8]
            if old:
                tx.put("mapping_history", mid + ":" + str(old["version"]), old)
            mapping = {
                **body.model_dump(),
                "id": mid,
                "version": old["version"] + 1 if old else 1,
                "status": "draft",
                "created_by": p["user"],
            }
            tx.put("mapping", mid, mapping)
            tx.audit(p["user"], "mapping.saved", mid, {"version": mapping["version"]})
            return mapping

    @app.post("/api/mappings/{mapping_id}/confirm")
    def confirm(mapping_id: str, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            mapping = required(tx, "mapping", mapping_id)
            _, errors = safe_read(lambda: preview_mapping(tx, root, mapping))
            if errors:
                raise DomainError("数据校验未通过，不能确认映射")
            mapping.update(status="confirmed", confirmed_by=p["user"], confirmed_at=now())
            tx.put("mapping", mapping_id, mapping)
            tx.audit(p["user"], "mapping.confirmed", mapping_id, {"version": mapping["version"]})
            return mapping

    @app.get("/api/ontology")
    def ontology(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            business = Business(tx)
            return {
                "entities": [
                    {
                        "id": k,
                        "label": LABELS[k],
                        "schema": v.model_json_schema(),
                        "count": len(business.rows(k)),
                    }
                    for k, v in ENTITIES.items()
                ],
                "relations": [
                    {"source": a, "field": b, "target": c, "label": d, "cardinality": e}
                    for a, b, c, d, e in RELATIONS
                ],
                "objects": business.objects,
                "issues": business.issues(),
                "resolutions": tx.all("resolution"),
            }

    @app.get("/api/objects/{object_id}/history")
    def history(object_id: str, p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            rows = [r for r in tx.all("revision") if r["id"] == object_id]
            if not rows:
                raise DomainError("对象历史不存在", 404)
            tx.audit(p["user"], "lineage.read", object_id)
            return rows

    @app.post("/api/resolutions/{issue_id}")
    def resolve(issue_id: str, body: Resolution, p=Depends(auth)):
        permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            business = Business(tx)
            issue = next((i for i in business.issues() if i["id"] == issue_id), None)
            if not issue:
                raise DomainError("待复核关联不存在", 404)
            target = required(tx, "object", body.target_id)
            if target["entity"] != issue["target_entity"]:
                raise DomainError("目标对象类型不匹配")
            value = {
                "id": issue_id,
                **body.model_dump(),
                "source_version": business.by_id[issue["object_id"]]["version"],
                "actor": p["user"],
                "at": now(),
            }
            tx.put("resolution", issue_id, value)
            tx.audit(p["user"], "relation.resolved", issue_id, value)
            return value

    @app.get("/api/capabilities")
    def capabilities(p=Depends(auth)):
        return [
            {
                **c,
                "input_schema": CapabilityArgs.model_json_schema(),
                "output_contract": {
                    "result": "业务结果",
                    "meta": "computed_at / rule_version / warnings / provenance",
                },
                "permission": "operator" if c["write"] else "viewer",
                "timeout_seconds": 30,
                "enabled": not c["write"] or p["role"] in ("operator", "admin"),
            }
            for c in CAPABILITIES
        ]

    @app.post("/api/capabilities/{name}/call")
    def capability(name: str, body: CapabilityArgs, p=Depends(auth)):
        if name == "propose_schedule_change":
            permit(p, "operator")
        with store.transaction(p["tenant"]) as tx:
            return call_capability(tx, name, body, p["user"])

    @app.get("/api/actions")
    def actions(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            return sorted(tx.all("action"), key=lambda a: a["created_at"], reverse=True)

    @app.post("/api/actions/{action_id}/decision")
    def decision(action_id: str, body: Decision, p=Depends(auth)):
        permit(p, "approver")
        with store.transaction(p["tenant"]) as tx:
            action = required(tx, "action", action_id)
            if action["created_by"] == p["user"]:
                raise DomainError("申请人与审批人必须分离", 403)
            if action["status"] != "pending_approval":
                raise DomainError("草案已处理，不能重复审批", 409)
            if body.decision == "approve":
                business = Business(tx)
                if (
                    {o["id"]: o["version"] for o in business.objects} != action["input_versions"]
                    or tx.all("resolution") != action["resolution_snapshot"]
                    or business.risk(business.order(action["order_id"]))["level"] == "unknown"
                ):
                    raise DomainError("输入数据或关联已变更/过期，请重新创建草案", 409)
            action["status"] = "approved_draft" if body.decision == "approve" else "rejected"
            action["history"].append(
                {"at": now(), "status": action["status"], "actor": p["user"], "reason": body.reason}
            )
            tx.put("action", action_id, action)
            tx.audit(
                p["user"], "action." + action["status"], action_id, {"reason": body.reason, "executed": False}
            )
            return action

    @app.get("/api/operations")
    def operations(p=Depends(auth)):
        with store.transaction(p["tenant"]) as tx:
            return {
                "runs": sorted(tx.all("run"), key=lambda r: r["at"], reverse=True)[:100],
                "audit": sorted(tx.all("audit"), key=lambda a: a["at"], reverse=True)[:200],
                "gateway": {
                    "mode": "local",
                    "source_modes": ["sqlite-readonly", "csv"],
                    "writeback": False,
                    "sync_mode": "bounded_full_snapshot",
                    "max_rows": 10000,
                },
            }

    from .v1.api import install

    install(app, store, root, auth, permit)

    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="console")
    return app
