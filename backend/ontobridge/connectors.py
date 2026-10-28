"""Bounded local gateway adapters. No source SQL is accepted from callers."""

import csv
import sqlite3
import time
from pathlib import Path

MAX_ROWS = 10000
MAX_BYTES = 10 * 1024 * 1024


def allowed_path(root: Path, relative: str):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("数据源必须是网关授权目录内的现有文件")
    return path


def database(path):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    deadline = time.monotonic() + 3
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return conn


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def csv_rows(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("CSV 超过 10 MiB 上限，请拆分文件")
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        columns = reader.fieldnames or []
        if not columns or len(columns) != len(set(columns)) or any(not c for c in columns):
            raise ValueError("CSV 列名为空或重复")
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("CSV 行列数量不一致")
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise ValueError("数据超过 10,000 行上限，请拆分数据源")
    return columns, rows


def discover(root, source):
    path = allowed_path(root, source["path"])
    if source["kind"] == "csv":
        cols, rows = csv_rows(path)
        return [{"name": path.stem, "columns": cols, "sample": rows[:3]}]
    conn = database(path)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        if len(tables) > 100:
            raise ValueError("最多发现 100 张表，请创建专用导出数据库")
        return [
            {
                "name": t[0],
                "columns": [c[1] for c in conn.execute(f"PRAGMA table_info({quote(t[0])})")],
                "sample": [dict(r) for r in conn.execute(f"SELECT * FROM {quote(t[0])} LIMIT 3")],
            }
            for t in tables
        ]
    finally:
        conn.close()


def read_rows(root, source, table):
    path = allowed_path(root, source["path"])
    schema = discover(root, source)
    if table not in [t["name"] for t in schema]:
        raise ValueError("映射表不在已发现的来源结构中")
    if source["kind"] == "csv":
        return csv_rows(path)[1]
    conn = database(path)
    try:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {quote(table)} LIMIT {MAX_ROWS + 1}")]
        if len(rows) > MAX_ROWS:
            raise ValueError("数据超过 10,000 行上限，请使用专用导出表")
        return rows
    finally:
        conn.close()
