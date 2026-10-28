"""Explicit synthetic fixtures. Generated only for the isolated demo tenant."""

import csv
import sqlite3
from datetime import timedelta

from .models import ENTITIES
from .service import sync_source
from .store import business_today, now


def seed(store, root):
    root.mkdir(parents=True, exist_ok=True)
    with store.transaction("demo") as tx:
        if tx.get("settings", "seeded"):
            return
        folder = root / "demo"
        folder.mkdir(exist_ok=True)
        database = folder / "erp.sqlite"
        # Never overwrite a user's source database.
        if database.exists():
            raise RuntimeError("演示 ERP 文件已存在但数据库未初始化，请使用新的数据目录")
        conn = sqlite3.connect(database)
        today = business_today()
        conn.executescript("""
        CREATE TABLE sale_order (order_no TEXT PRIMARY KEY, customer TEXT, style_no TEXT, qty INTEGER, delivery_date TEXT, price REAL);
        CREATE TABLE item_master (style_no TEXT PRIMARY KEY, style_name TEXT, material_no TEXT, consumption REAL);
        CREATE TABLE material (id TEXT PRIMARY KEY, name TEXT, unit TEXT);
        CREATE TABLE resource (id TEXT PRIMARY KEY, name TEXT, daily_hours REAL);
        """)
        products = [
            ("ST-101", "轻户外防风夹克", "MAT-01", 1.8),
            ("ST-102", "棉质工装衬衫", "MAT-02", 1.4),
            ("ST-103", "城市通勤长裤", "MAT-03", 1.2),
        ]
        conn.executemany("INSERT INTO item_master VALUES (?,?,?,?)", products)
        conn.executemany(
            "INSERT INTO material VALUES (?,?,?)",
            [("MAT-01", "防水复合面料", "米"), ("MAT-02", "精梳纯棉", "米"), ("MAT-03", "弹力斜纹布", "米")],
        )
        conn.executemany(
            "INSERT INTO resource VALUES (?,?,?)",
            [
                ("LINE-A", "A 线 · 外套组", 16),
                ("LINE-B", "B 线 · 梭织组", 16),
                ("LINE-C", "C 线 · 快反组", 12),
            ],
        )
        customers = [
            "山川户外",
            "栖木服饰",
            "远行制造",
            "北岸生活",
            "山川户外",
            "织野设计",
            "栖木服饰",
            "北岸生活",
        ]
        work, ops = [], []
        for i, customer in enumerate(customers):
            oid = f"SO-2026-{88 + i:04}"
            quantity = [1200, 800, 1600, 600, 1000, 750, 900, 500][i]
            conn.execute(
                "INSERT INTO sale_order VALUES (?,?,?,?,?,?)",
                (
                    oid,
                    customer,
                    products[i % 3][0],
                    quantity,
                    (today + timedelta(days=[2, 3, 4, 5, 6, 8, 10, 12][i])).isoformat(),
                    [128, 89, 110][i % 3],
                ),
            )
            for part in range(2):
                wid = f"MO-{231 + i * 2 + part:05}"
                qty = quantity // 2
                finished = int(qty * [0.72, 0.85, 0.35, 0.9, 0.2, 0.5, 0.3, 0.6][i])
                work.append(
                    {
                        "task_no": wid,
                        "order_ref": oid,
                        "line": ["LINE-A", "LINE-B", "LINE-C"][i % 3],
                        "planned_qty": qty,
                        "finished_qty": finished,
                    }
                )
                ops.append(
                    {
                        "id": f"OP-{i}-{part}",
                        "work_order_id": wid,
                        "name": "缝制 / 整烫 / 终检",
                        "remaining_hours": [20, 8, 25, 5, 24, 18, 21, 9][i],
                    }
                )
        conn.commit()
        conn.close()
        files = [
            ("mes_work_orders.csv", work),
            ("mes_operations.csv", ops),
            (
                "wms_stock.csv",
                [
                    {"id": "INV-01", "material_id": "MAT-01", "warehouse": "面料主仓", "available": 1300},
                    {"id": "INV-02", "material_id": "MAT-02", "warehouse": "面料主仓", "available": 2000},
                    {"id": "INV-03", "material_id": "MAT-03", "warehouse": "快反仓", "available": 600},
                ],
            ),
        ]
        for name, rows in files:
            with (folder / name).open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        sources = [
            ("src-erp", "华织 ERP", "ERP", "sqlite", "erp.sqlite"),
            ("src-mes", "制造 MES · 工单", "MES", "csv", "mes_work_orders.csv"),
            ("src-ops", "制造 MES · 工序", "MES", "csv", "mes_operations.csv"),
            ("src-wms", "仓储 WMS", "WMS", "csv", "wms_stock.csv"),
        ]
        for sid, name, system, kind, path in sources:
            tx.put(
                "source",
                sid,
                {
                    "id": sid,
                    "name": name,
                    "system": system,
                    "kind": kind,
                    "path": "demo/" + path,
                    "freshness_minutes": 1440,
                    "status": "configured",
                    "created_at": now(),
                    "demo": True,
                },
            )
        maps = [
            (
                "src-erp",
                "sale_order",
                "SalesOrder",
                dict(
                    id="order_no",
                    customer="customer",
                    product_id="style_no",
                    quantity="qty",
                    due_date="delivery_date",
                    unit_price="price",
                ),
            ),
            (
                "src-erp",
                "item_master",
                "Product",
                dict(
                    id="style_no",
                    name="style_name",
                    material_id="material_no",
                    material_per_unit="consumption",
                ),
            ),
            ("src-erp", "material", "Material", None),
            ("src-erp", "resource", "Resource", None),
            (
                "src-mes",
                "mes_work_orders",
                "WorkOrder",
                dict(
                    id="task_no",
                    order_id="order_ref",
                    resource_id="line",
                    quantity="planned_qty",
                    finished="finished_qty",
                ),
            ),
            ("src-ops", "mes_operations", "Operation", None),
            ("src-wms", "wms_stock", "Inventory", None),
        ]
        for i, (sid, table, entity, fields) in enumerate(maps):
            mid = f"map-{i + 1}"
            tx.put(
                "mapping",
                mid,
                {
                    "id": mid,
                    "source_id": sid,
                    "table": table,
                    "entity": entity,
                    "fields": fields or {f: f for f in ENTITIES[entity].model_fields},
                    "version": 1,
                    "status": "confirmed",
                    "confirmed_by": "demo-seed",
                    "confirmed_at": now(),
                },
            )
        for source in sources:
            sync_source(tx, root, source[0], "demo-seed")
        tx.put("settings", "seeded", {"at": now(), "synthetic": True})
