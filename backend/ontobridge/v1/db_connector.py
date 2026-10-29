"""SQLAlchemy SELECT-only database adapters with server-enforced read-only boundaries."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import MetaData, Table, and_, create_engine, inspect, or_, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from ..service import DomainError
from .connector_sdk import Connector, json_value, timestamp
from .contracts import ReadBatch, SourceColumn, TableSchema


class DatabaseConnector(Connector):
    kinds = ("postgresql", "mysql", "mssql", "sqlite")
    incremental = True

    @contextmanager
    def connection(self):
        kind = self.context.source["kind"]
        if kind == "sqlite":
            path = self.context.path()
            engine = create_engine(
                "sqlite+pysqlite://",
                creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True),
                poolclass=NullPool,
            )
        else:
            config = self.context.profile()
            driver = {"postgresql": "postgresql+psycopg", "mysql": "mysql+pymysql", "mssql": "mssql+pymssql"}[
                kind
            ]
            args = (
                {"connect_timeout": 5}
                if kind in ("postgresql", "mysql")
                else {"login_timeout": 5, "timeout": 15}
            )
            if kind == "mysql":
                args.update(read_timeout=15, write_timeout=15)
            if kind == "postgresql":
                args.update(sslmode=config.get("sslmode", "require"))
            if kind == "mysql" and config.get("ssl_ca"):
                args.update(ssl={"ca": config["ssl_ca"], "check_hostname": True})
            if kind == "mssql":
                args.update(encryption=config.get("encryption", "require"), read_only=True)
            url = URL.create(
                driver,
                username=config["username"],
                password=config["password"],
                host=config["host"],
                port=config.get("port"),
                database=config["database"],
            )
            engine = create_engine(url, connect_args=args, poolclass=NullPool, hide_parameters=True)
        try:
            with engine.connect() as connection, connection.begin():
                if kind == "postgresql":
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    connection.execute(text("SET LOCAL statement_timeout = '15000ms'"))
                elif kind == "mysql":
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                elif kind == "sqlite":
                    connection.execute(text("PRAGMA query_only=ON"))
                else:
                    connection.execute(text("SET LOCK_TIMEOUT 15000"))
                yield connection
        except SQLAlchemyError as exc:
            # Do not leak DSNs, driver text, values, usernames, or passwords into API/audit logs.
            raise DomainError(f"{kind} 连接或只读查询失败，请检查授权、网络和服务端日志", 502) from exc
        finally:
            engine.dispose()

    def schema_name(self):
        return self.context.source.get("schema_name")

    def table_names(self, connection):
        names = inspect(connection).get_table_names(schema=self.schema_name())
        requested = self.context.source.get("tables", [])
        if requested and not set(requested) <= set(names):
            raise DomainError("授权表清单包含不存在或不可访问的表")
        names = requested or names
        if len(names) > 100:
            raise DomainError("发现超过 100 张表，请配置明确的 tables 清单")
        return names

    def reflect(self, connection, name):
        if name not in self.table_names(connection):
            raise DomainError("表不在已发现且授权的范围中")
        table = Table(name, MetaData(), schema=self.schema_name(), autoload_with=connection)
        if self.context.source["kind"] == "mssql":
            # SQL Server has no PostgreSQL-style transaction READ ONLY. Require a real read-only account.
            qualified = f"{self.schema_name() or 'dbo'}.{name}"
            for permission in ("INSERT", "UPDATE", "DELETE", "ALTER", "CONTROL"):
                allowed = connection.execute(
                    text("SELECT HAS_PERMS_BY_NAME(:object, :class, :permission)"),
                    {"object": qualified, "class": "OBJECT", "permission": permission},
                ).scalar()
                if allowed is None or allowed != 0:
                    raise DomainError("SQL Server 来源账号必须撤销目标表写入、修改和控制权限", 403)
        return table

    def discover(self):
        with self.connection() as conn:
            result = []
            for name in self.table_names(conn):
                table = self.reflect(conn, name)
                sample = [
                    {key: json_value(value) for key, value in row.items()}
                    for row in conn.execute(select(table).limit(3)).mappings()
                ]
                result.append(
                    TableSchema(
                        name=name,
                        columns=[
                            SourceColumn(
                                name=c.name, type=str(c.type), nullable=c.nullable, primary_key=c.primary_key
                            )
                            for c in table.c
                        ],
                        primary_key=[c.name for c in table.primary_key],
                        sample=sample,
                    )
                )
            return result

    def read(self, table, primary_key, batch_size, cursor=None, checkpoint=None):
        with self.connection() as conn:
            obj = self.reflect(conn, table)
            if primary_key not in obj.c or (cursor and cursor["column"] not in obj.c):
                raise DomainError("主键或增量字段不在来源结构中")
            # All cursors are keyset-based. Never use OFFSET, which can skip concurrent inserts.
            inspector = inspect(conn)
            kind = self.context.source["kind"]
            # MSSQL reflects UNIQUE constraints as indexes; its constraint API is unimplemented.
            constraints = (
                [] if kind == "mssql" else inspector.get_unique_constraints(table, schema=self.schema_name())
            )
            unique_keys = [set(c.get("column_names", [])) for c in constraints]
            indexes = inspector.get_indexes(table, schema=self.schema_name())
            if kind == "mssql":
                # Read-only users may not see filter_definition. Check has_filter directly,
                # since a filtered unique index does not guarantee uniqueness of every row.
                preparer = conn.dialect.identifier_preparer
                qualified = f"{preparer.quote(self.schema_name() or 'dbo')}.{preparer.quote(table)}"
                unfiltered = set(
                    conn.execute(
                        text(
                            "SELECT name FROM sys.indexes WHERE object_id=OBJECT_ID(:object) "
                            "AND is_unique=1 AND has_filter=0 AND is_disabled=0"
                        ),
                        {"object": qualified},
                    ).scalars()
                )
                indexes = [index for index in indexes if index["name"] in unfiltered]
            unique_keys += [
                set(c.get("column_names", []))
                for c in indexes
                if c.get("unique") and not any(key.endswith("_where") for key in c.get("dialect_options", {}))
            ]
            unique_keys.append({c.name for c in obj.primary_key})
            if {primary_key} not in unique_keys:
                raise DomainError("同步主键必须在来源数据库中具有单列 PRIMARY KEY 或 UNIQUE 约束")
            pk = obj.c[primary_key]
            query = select(obj)
            ordering = [pk]
            if cursor:
                water = obj.c[cursor["column"]]
                if conn.execute(select(pk).where(water.is_(None)).limit(1)).first():
                    raise DomainError("增量列包含 NULL，无法建立完整水位，请先修复来源或使用全量")
                ordering = [water, pk]
                if checkpoint:
                    value = checkpoint["value"]
                    if cursor["kind"] == "datetime":
                        value = datetime.fromisoformat(value)
                    elif cursor["kind"] == "integer":
                        value = int(value)
                    query = (
                        query.where(water >= value)
                        if checkpoint.get("key") is None
                        else query.where(or_(water > value, and_(water == value, pk > checkpoint["key"])))
                    )
                query = query.where(water.is_not(None))
            elif checkpoint:
                query = query.where(pk > checkpoint["key"])
            rows = [dict(r) for r in conn.execute(query.order_by(*ordering).limit(batch_size + 1)).mappings()]
            exhausted = len(rows) <= batch_size
            rows = rows[:batch_size]
            if any(r[primary_key] is None for r in rows):
                raise DomainError("主键不能为空")
            if len({str(r[primary_key]) for r in rows}) != len(rows):
                raise DomainError("批次出现重复主键，请确认来源唯一约束")
            if rows:
                checkpoint = {"key": json_value(rows[-1][primary_key])}
                if cursor:
                    checkpoint["value"] = json_value(rows[-1][cursor["column"]])
            updated = timestamp()
            if self.context.source["kind"] == "sqlite":
                updated = datetime.fromtimestamp(self.context.path().stat().st_mtime, updated.tzinfo)
            return ReadBatch(
                rows=[{k: json_value(v) for k, v in row.items()} for row in rows],
                checkpoint=checkpoint,
                exhausted=exhausted,
                source_updated_at=updated,
                freshness_basis="source_file_mtime"
                if self.context.source["kind"] == "sqlite"
                else "live_database_observation",
            )
