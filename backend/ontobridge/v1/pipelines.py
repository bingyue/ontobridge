"""Durable single-source jobs: leased claims, staged batches, atomic promotion and checkpoint commit."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ..service import DomainError, required
from ..store import now
from .connector_sdk import ConnectorContext
from .rpa import prepare_source


def later(seconds):
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def enqueue(tx, pipeline_id, actor, force_full=False):
    pipeline = required(tx, "v1_pipeline", pipeline_id)
    current = next(
        (
            r
            for r in tx.all("v1_run")
            if r["pipeline_id"] == pipeline_id and r["status"] in ("queued", "running", "retry_wait")
        ),
        None,
    )
    if current:
        return current
    key = "run-" + uuid4().hex
    checkpoint = pipeline.get("checkpoint") if pipeline["mode"] == "incremental" and not force_full else None
    if checkpoint:
        # Re-read the last watermark boundary on each poll. Equal-timestamp updates cannot be skipped.
        checkpoint = {**checkpoint, "key": None}
    body = {
        "id": key,
        "pipeline_id": pipeline_id,
        "source_id": pipeline["source_id"],
        "pipeline_version": pipeline["version"],
        "mode": "full" if force_full else pipeline["mode"],
        "status": "queued",
        "attempts": 0,
        "created_at": now(),
        "actor": actor,
        "checkpoint": checkpoint,
        "base_checkpoint": checkpoint,
        "staged": 0,
        "batch_count": 0,
        "next_attempt_at": now(),
        "lease_owner": None,
        "lease_until": None,
        "error": None,
    }
    tx.put("v1_run", key, body)
    pipeline["last_enqueued_at"] = now()
    tx.put("v1_pipeline", pipeline_id, pipeline)
    tx.audit(actor, "pipeline.enqueued", key, {"pipeline_id": pipeline_id, "mode": body["mode"]})
    return body


class GatewayWorker:
    def __init__(self, store, root, registry, profiles=None, worker_id=None):
        self.store, self.root, self.registry, self.profiles = store, root, registry, profiles
        self.id = worker_id or "worker-" + uuid4().hex

    def schedule(self, tenant):
        with self.store.transaction(tenant) as tx:
            tx.put("v1_worker", self.id, {"id": self.id, "last_seen": now(), "run_id": None})
            for p in tx.all("v1_pipeline"):
                if p["enabled"] and p["poll_seconds"]:
                    last = datetime.fromisoformat(p.get("last_enqueued_at", p["created_at"]))
                    if (datetime.now(UTC) - last).total_seconds() >= p["poll_seconds"]:
                        enqueue(tx, p["id"], "scheduler")

    def claim(self, tenant):
        with self.store.transaction(tenant) as tx:
            for run in sorted(tx.all("v1_run"), key=lambda r: r["created_at"]):
                eligible = run["status"] in ("queued", "retry_wait") and run["next_attempt_at"] <= now()
                expired = run["status"] == "running" and run["lease_until"] <= now()
                owned = run["status"] == "running" and run["lease_owner"] == self.id
                if not (eligible or expired or owned):
                    continue
                pipeline = required(tx, "v1_pipeline", run["pipeline_id"])
                if not owned and run["attempts"] >= pipeline["max_attempts"]:
                    run.update(
                        status="failed",
                        error="网关租约连续失效，已达到最大尝试次数",
                        lease_owner=None,
                        lease_until=None,
                        finished_at=now(),
                    )
                    tx.put("v1_run", run["id"], run)
                    tx.put(
                        "v1_alert",
                        run["id"],
                        {
                            "id": run["id"],
                            "pipeline_id": pipeline["id"],
                            "run_id": run["id"],
                            "status": "open",
                            "message": run["error"],
                            "at": now(),
                        },
                    )
                    pipeline["last_error"] = run["error"]
                    tx.put("v1_pipeline", pipeline["id"], pipeline)
                    source = required(tx, "v1_source", pipeline["source_id"])
                    source.update(status="error", last_error=run["error"])
                    tx.put("v1_source", source["id"], source)
                    tx.audit("gateway", "pipeline.failed", run["id"], {"error": run["error"]})
                    continue
                if not owned:
                    run["attempts"] += 1
                run.update(status="running", lease_owner=self.id, lease_until=later(60), updated_at=now())
                tx.put("v1_run", run["id"], run)
                pipeline = required(tx, "v1_pipeline", run["pipeline_id"])
                source = required(tx, "v1_source", pipeline["source_id"])
                tx.put("v1_worker", self.id, {"id": self.id, "last_seen": now(), "run_id": run["id"]})
                return run, pipeline, source
        return None

    def tick(self, tenant=None):
        for tenant_id in [tenant] if tenant else self.store.tenants():
            self.schedule(tenant_id)
            claimed = self.claim(tenant_id)
            if not claimed:
                continue
            run, pipeline, source = claimed
            try:
                if pipeline["version"] != run["pipeline_version"]:
                    raise DomainError("运行期间流水线版本发生变化", 409)
                with self.store.transaction(tenant_id) as tx:
                    source = prepare_source(tx, source)
                source["_run_id"] = run["id"]
                connector = self.registry.create(
                    ConnectorContext(self.root, tenant_id, source, self.profiles)
                )
                batch = connector.read(
                    pipeline["table"],
                    pipeline["primary_key"],
                    pipeline["batch_size"],
                    pipeline["cursor"] if run["mode"] == "incremental" else None,
                    run["checkpoint"],
                )
                self.stage(tenant_id, run, pipeline, source, batch)
            except Exception as exc:
                self.fail(tenant_id, run, pipeline, exc)
            return True
        return False

    def stage(self, tenant, claimed, pipeline, source, batch):
        with self.store.transaction(tenant) as tx:
            run = required(tx, "v1_run", claimed["id"])
            if run["lease_owner"] != self.id or run["status"] != "running" or run["lease_until"] <= now():
                raise DomainError("任务租约已失效，本批次未写入", 409)
            if required(tx, "v1_pipeline", pipeline["id"])["version"] != run["pipeline_version"]:
                raise DomainError("流水线版本变化，本批次未写入", 409)
            for row in batch.rows:
                pk = str(row[pipeline["primary_key"]])
                record_id = hashlib.sha256(pk.encode()).hexdigest()
                stage_id = run["id"] + ":" + record_id
                existing = tx.get("v1_stage", stage_id)
                if existing and run["mode"] == "full":
                    raise DomainError("同一快照出现重复业务主键；禁止自动覆盖", 409)
                tx.put(
                    "v1_stage",
                    stage_id,
                    {
                        "id": stage_id,
                        "record_id": record_id,
                        "run_id": run["id"],
                        "source_key": pk,
                        "data": row,
                        "source_updated_at": batch.source_updated_at.isoformat(),
                        "freshness_basis": batch.freshness_basis,
                    },
                )
            run.update(
                checkpoint=batch.checkpoint,
                staged=run["staged"] + len(batch.rows),
                batch_count=run["batch_count"] + 1,
                lease_until=later(60),
                updated_at=now(),
            )
            tx.put("v1_run", run["id"], run)
            if batch.exhausted:
                self.promote(tx, run, pipeline, source)

    def promote(self, tx, run, pipeline, source):
        staged = [r for r in tx.all("v1_stage") if r["run_id"] == run["id"]]
        active, changed = set(), 0
        for row in staged:
            key = pipeline["id"] + ":" + row["record_id"]
            active.add(key)
            old = tx.get("v1_asset", key)
            digest = hashlib.sha256(
                json.dumps(row["data"], sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            ).hexdigest()
            changed += int(not old or old["digest"] != digest)
            tx.put(
                "v1_asset",
                key,
                {
                    "id": key,
                    "pipeline_id": pipeline["id"],
                    "source_id": source["id"],
                    "table": pipeline["table"],
                    "source_key": row["source_key"],
                    "data": row["data"],
                    "digest": digest,
                    "run_id": run["id"],
                    "synced_at": now(),
                    "source_updated_at": row["source_updated_at"],
                    "freshness_basis": row["freshness_basis"],
                },
            )
        removed = 0
        if run["mode"] == "full":
            for asset in tx.all("v1_asset"):
                if asset["pipeline_id"] == pipeline["id"] and asset["id"] not in active:
                    tx.remove("v1_asset", asset["id"])
                    removed += 1
        # Watermark and data become visible in exactly the same transaction.
        if run["mode"] == "incremental" and run["checkpoint"]:
            pipeline["checkpoint"] = run["checkpoint"]
        elif run["mode"] == "full" and pipeline["mode"] == "incremental":
            pipeline["checkpoint"] = None  # Next incremental pass rebuilds the high-water mark safely.
        pipeline.update(last_success_at=now(), last_error=None)
        tx.put("v1_pipeline", pipeline["id"], pipeline)
        source.update(status="connected", synced_at=now(), last_error=None)
        tx.put("v1_source", source["id"], source)
        run.update(
            status="succeeded",
            finished_at=now(),
            changed=changed,
            removed=removed,
            lease_until=None,
            error=None,
        )
        tx.put("v1_run", run["id"], run)
        for row in staged:
            tx.remove("v1_stage", row["id"])
        for alert in tx.all("v1_alert"):
            if alert["pipeline_id"] == pipeline["id"] and alert["status"] == "open":
                alert.update(status="resolved", resolved_at=now())
                tx.put("v1_alert", alert["id"], alert)
        tx.audit(
            run["actor"],
            "pipeline.succeeded",
            run["id"],
            {"records": run["staged"], "changed": changed, "removed": removed, "batches": run["batch_count"]},
        )

    def fail(self, tenant, claimed, pipeline, exc):
        with self.store.transaction(tenant) as tx:
            run = required(tx, "v1_run", claimed["id"])
            if run["lease_owner"] != self.id or run["status"] != "running":
                return
            message = exc.message if isinstance(exc, DomainError) else "连接器执行失败，请检查网关日志及配置"
            retry = run["attempts"] < pipeline["max_attempts"]
            run.update(
                status="retry_wait" if retry else "failed",
                error=message,
                lease_owner=None,
                lease_until=None,
                next_attempt_at=later(min(3600, pipeline["retry_seconds"] * 2 ** (run["attempts"] - 1))),
                updated_at=now(),
            )
            # Restart cleanly when file fingerprint changes. DB retries retain staged batches/checkpoint.
            if isinstance(exc, DomainError) and exc.status == 409:
                for row in tx.all("v1_stage"):
                    if row["run_id"] == run["id"]:
                        tx.remove("v1_stage", row["id"])
                run.update(checkpoint=run["base_checkpoint"], staged=0, batch_count=0)
            tx.put("v1_run", run["id"], run)
            pipeline.update(last_error=message)
            tx.put("v1_pipeline", pipeline["id"], pipeline)
            source = required(tx, "v1_source", pipeline["source_id"])
            source.update(status="error", last_error=message)
            tx.put("v1_source", source["id"], source)
            alert = {
                "id": run["id"],
                "pipeline_id": pipeline["id"],
                "run_id": run["id"],
                "status": "open",
                "message": message,
                "at": now(),
            }
            tx.put("v1_alert", alert["id"], alert)
            tx.audit(
                "gateway",
                "pipeline.retry" if retry else "pipeline.failed",
                run["id"],
                {"error": message, "attempt": run["attempts"]},
            )
