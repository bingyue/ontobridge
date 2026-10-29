"""Reproducible, isolated REAL database servers containing synthetic ERP fixtures.
Never connects to existing enterprise databases. Credentials remain in var/v1-integration (0600).
"""

import argparse
import json
import os
import secrets
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from ontobridge.service import DomainError
from ontobridge.v1.connector_sdk import ConnectorContext
from ontobridge.v1.db_connector import DatabaseConnector
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "var/v1-integration"


def docker(args, input=None, env=None, check=True):
    result = subprocess.run(["docker", *args], input=input, text=True, capture_output=True, env=env)
    if check and result.returncode:
        raise RuntimeError("Docker operation failed: " + result.stderr[-800:])
    return result


def settings():
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / "credentials.json"
    if path.exists():
        return json.loads(path.read_text())
    value = {kind: "Aa1!" + secrets.token_urlsafe(24) for kind in ["postgresql", "mysql", "mssql", "reader"]}
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f)
    return value


def start(kind, credential):
    name = "ontobridge-v1-" + kind
    running = docker(["inspect", name, "--format", "{{.State.Status}}"], check=False)
    if running.returncode == 0:
        if running.stdout.strip() not in ("running", "created"):
            raise RuntimeError(
                f"{name} is {running.stdout.strip()}; inspect its logs before deciding recovery"
            )
        return name
    env = dict(os.environ)
    if kind == "postgresql":
        env["POSTGRES_PASSWORD"] = credential
        args = [
            "--memory",
            "384m",
            "-e",
            "POSTGRES_PASSWORD",
            "-p",
            "127.0.0.1:15533:5432",
            "postgres:16-alpine",
        ]
    elif kind == "mysql":
        env["MYSQL_ROOT_PASSWORD"] = credential
        args = ["--memory", "640m", "-e", "MYSQL_ROOT_PASSWORD", "-p", "127.0.0.1:13316:3306", "mysql:8.4"]
    else:
        env["MSSQL_SA_PASSWORD"] = credential
        args = [
            "--platform",
            "linux/amd64",
            "--memory",
            "2300m",
            "--cpus",
            "1.5",
            "-e",
            "ACCEPT_EULA=Y",
            "-e",
            "MSSQL_SA_PASSWORD",
            "-p",
            "127.0.0.1:11433:1433",
            "mcr.microsoft.com/mssql/server:2022-latest",
        ]
    docker(["run", "-d", "--name", name, *args], env=env)
    return name


def sql(kind, name, query):
    if kind == "postgresql":
        command = ["psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"]
    elif kind == "mysql":
        command = ["sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot']
    else:
        command = [
            "sh",
            "-c",
            'SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -I -b -l 5 -t 15',
        ]
    return docker(["exec", "-i", name, *command], input=query, check=False)


def provision(kind, name, reader, startup_timeout=180):
    deadline = time.monotonic() + startup_timeout
    while True:
        state = docker(["inspect", name, "--format", "{{.State.Status}}"]).stdout.strip()
        if state != "running":
            raise RuntimeError(f"{name} stopped ({state}); do not silently replace it")
        if sql(kind, name, "SELECT 1;\n").returncode == 0:
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"{name} still running but not ready; inspect and resume same container")
        time.sleep(2)
    if kind == "postgresql":
        query = f"""
CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, customer TEXT NOT NULL, updated INTEGER NOT NULL);
TRUNCATE orders;
INSERT INTO orders VALUES (1,'Mock A',10),(2,'Mock B',10),(3,'Mock C',11);
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='obr_reader') THEN CREATE ROLE obr_reader LOGIN PASSWORD '{reader}'; END IF; END $$;
GRANT CONNECT ON DATABASE postgres TO obr_reader;
GRANT USAGE ON SCHEMA public TO obr_reader;
GRANT SELECT ON orders TO obr_reader;
"""
    elif kind == "mysql":
        query = f"""
CREATE DATABASE IF NOT EXISTS erp;
USE erp;
CREATE TABLE IF NOT EXISTS orders (id INT PRIMARY KEY, customer VARCHAR(100) NOT NULL, updated INT NOT NULL);
TRUNCATE orders;
INSERT INTO orders VALUES (1,'Mock A',10),(2,'Mock B',10),(3,'Mock C',11);
CREATE USER IF NOT EXISTS 'obr_reader'@'%' IDENTIFIED BY '{reader}';
GRANT SELECT ON erp.* TO 'obr_reader'@'%';
"""
    else:
        query = f"""
IF DB_ID('erp') IS NULL CREATE DATABASE erp;
GO
USE erp;
IF OBJECT_ID('orders') IS NULL CREATE TABLE orders (id INT PRIMARY KEY, customer NVARCHAR(100) NOT NULL, updated INT NOT NULL);
TRUNCATE TABLE orders;
INSERT INTO orders VALUES (1,'Mock A',10),(2,'Mock B',10),(3,'Mock C',11);
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name='obr_reader') CREATE LOGIN obr_reader WITH PASSWORD='{reader}';
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='unique_customer' AND object_id=OBJECT_ID('orders')) CREATE UNIQUE INDEX unique_customer ON orders(customer);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='partial_watermark' AND object_id=OBJECT_ID('orders')) CREATE UNIQUE INDEX partial_watermark ON orders(updated) WHERE id > 1;
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name='obr_reader') CREATE USER obr_reader FOR LOGIN obr_reader;
ALTER ROLE db_datareader ADD MEMBER obr_reader;
GO
"""
    result = sql(kind, name, query)
    if result.returncode:
        raise RuntimeError("Fixture provisioning failed: " + result.stderr[-400:])


def verify(kind, reader, admin_password=None):
    profile = {
        "kind": kind,
        "host": "127.0.0.1",
        "port": {"postgresql": 15533, "mysql": 13316, "mssql": 11433}[kind],
        "username": "obr_reader",
        "password": reader,
        "database": "postgres" if kind == "postgresql" else "erp",
        "sslmode": "disable",
        "encryption": "off",
    }
    profiles = {"mock-factory": {kind: profile}}
    source = {"kind": kind, "connection_ref": kind, "tables": ["orders"]}
    connector = DatabaseConnector(ConnectorContext(STATE, "mock-factory", source, profiles))
    schema = connector.discover()[0]
    assert schema.primary_key == ["id"]
    columns = {column.name: column for column in schema.columns}
    assert set(columns) == {"id", "customer", "updated"}
    assert all("INT" in columns[name].type.upper() for name in ("id", "updated"))
    assert any(part in columns["customer"].type.upper() for part in ("CHAR", "TEXT"))
    assert all(not column.nullable for column in columns.values())
    cursor = {"column": "updated", "tie_breaker": "id", "kind": "integer"}
    first = connector.read("orders", "id", 2, cursor)
    second = connector.read("orders", "id", 2, cursor, first.checkpoint)
    assert [r["id"] for r in first.rows + second.rows] == [1, 2, 3]
    assert not first.exhausted and second.exhausted
    try:
        with connector.connection() as connection:
            connection.execute(text("DELETE FROM orders"))
    except DomainError as exc:
        original = getattr(exc.__cause__, "orig", None)
        error_code = (
            getattr(original, "sqlstate", None)
            if kind == "postgresql"
            else original.args[0]
            if original and original.args
            else None
        )
        expected = {"postgresql": {"25006", "42501"}, "mysql": {1142, 1792}, "mssql": {229}}
        assert error_code in expected[kind], "DELETE failed for an unexpected reason, not proven read-only"
    else:
        raise AssertionError("read-only account unexpectedly allowed DELETE")
    assert len(connector.read("orders", "id", 10).rows) == 3
    if kind == "mssql":
        unique_page = connector.read("orders", "customer", 2)
        unique_rest = connector.read("orders", "customer", 2, checkpoint=unique_page.checkpoint)
        assert [r["id"] for r in unique_page.rows + unique_rest.rows] == [1, 2, 3]
        try:
            connector.read("orders", "updated", 2)
        except DomainError as exc:
            assert exc.status == 422 and "单列 PRIMARY KEY 或 UNIQUE" in exc.message
        else:
            raise AssertionError("Filtered unique index cannot prove global key uniqueness")
        assert admin_password is not None
        elevated = {"mock-factory": {kind: {**profile, "username": "sa", "password": admin_password}}}
        try:
            DatabaseConnector(ConnectorContext(STATE, "mock-factory", source, elevated)).discover()
        except DomainError as exc:
            assert exc.status == 403, "Writable source account was not rejected by the permission gate"
        else:
            raise AssertionError("SQL Server source unexpectedly accepted a write-capable account")
    (STATE / (kind + "-receipt.json")).write_text(
        json.dumps(
            {
                "synthetic_fixture": True,
                "database_kind": kind,
                "verified_at": datetime.now(UTC).isoformat(),
                "docker_context": docker(["context", "show"]).stdout.strip(),
                "docker_architecture": docker(["info", "--format", "{{.Architecture}}"]).stdout.strip(),
                "primary_key": schema.primary_key,
                "column_types": {name: column.type for name, column in columns.items()},
                "row_ids": [r["id"] for r in first.rows + second.rows],
                "write_denied_error_code": error_code,
                "write_capable_account_rejected": True if kind == "mssql" else None,
                "unique_and_filtered_index_checked": True if kind == "mssql" else None,
                "passed": True,
            },
            indent=2,
        )
    )
    print(
        f"{kind}: PASS real connection, type/PK discovery, tie-safe incremental pages, source write rejection"
    )
    path = STATE / "profiles.json"
    merged = json.loads(path.read_text()) if path.exists() else {"mock-factory": {}}
    merged["mock-factory"][kind] = profile
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(merged, f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kinds", nargs="+", choices=["postgresql", "mysql", "mssql"])
    parser.add_argument("--startup-timeout", type=int, default=180)
    args = parser.parse_args()
    if not 30 <= args.startup_timeout <= 1800:
        parser.error("--startup-timeout must be between 30 and 1800 seconds")
    credentials = settings()
    for kind in args.kinds:
        name = start(kind, credentials[kind])
        provision(kind, name, credentials["reader"], args.startup_timeout)
        verify(kind, credentials["reader"], credentials[kind])


if __name__ == "__main__":
    main()
