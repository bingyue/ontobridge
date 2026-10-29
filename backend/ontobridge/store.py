"""Tenant-scoped durable documents; all mutations commit atomically with audit events."""

import os
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, delete, select, text, update


def now():
    return datetime.now(UTC).isoformat()


def business_today():
    return datetime.now(ZoneInfo(os.environ.get("ONTOBRIDGE_TIMEZONE", "Asia/Shanghai"))).date()


class Store:
    def __init__(self, url):
        self.engine = create_engine(
            url, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
        )
        self.lock = RLock()
        meta = MetaData()
        self.docs = Table(
            "documents",
            meta,
            Column("tenant", String(100), primary_key=True),
            Column("kind", String(40), primary_key=True),
            Column("key", String(200), primary_key=True),
            Column("body", JSON, nullable=False),
        )
        meta.create_all(self.engine)

    @contextmanager
    def transaction(self, tenant):
        # Cross-process transaction serialization for API + independent gateway workers.
        with self.lock, self.engine.connect() as conn:
            try:
                if self.engine.dialect.name == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    conn.begin()
                    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:tenant))"), {"tenant": tenant})
                yield Transaction(self.docs, conn, tenant)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def tenants(self):
        with self.engine.connect() as conn:
            return list(conn.execute(select(self.docs.c.tenant).distinct()).scalars())


class Transaction:
    def __init__(self, table, conn, tenant):
        self.table, self.conn, self.tenant = table, conn, tenant

    def where(self, kind, key=None):
        clause = (self.table.c.tenant == self.tenant) & (self.table.c.kind == kind)
        return clause if key is None else clause & (self.table.c.key == key)

    def get(self, kind, key):
        return self.conn.execute(select(self.table.c.body).where(self.where(kind, key))).scalar_one_or_none()

    def all(self, kind):
        return list(
            self.conn.execute(
                select(self.table.c.body).where(self.where(kind)).order_by(self.table.c.key)
            ).scalars()
        )

    def put(self, kind, key, body):
        if self.get(kind, key) is None:
            self.conn.execute(self.table.insert().values(tenant=self.tenant, kind=kind, key=key, body=body))
        else:
            self.conn.execute(update(self.table).where(self.where(kind, key)).values(body=body))
        return body

    def remove(self, kind, key):
        self.conn.execute(delete(self.table).where(self.where(kind, key)))

    def audit(self, actor, event, target, detail=None):
        key = now() + "-" + uuid4().hex[:8]
        self.put(
            "audit",
            key,
            {
                "id": key,
                "at": now(),
                "actor": actor,
                "event": event,
                "target": target,
                "detail": detail or {},
            },
        )
