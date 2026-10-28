import hashlib
import math
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from .connectors import MAX_ROWS, allowed_path, discover, read_rows
from .models import ENTITIES, RELATIONS
from .store import business_today, now


class DomainError(Exception):
    def __init__(self, message, status=422):
        self.message, self.status = message, status


def required(tx, kind, key):
    item = tx.get(kind, key)
    if item is None:
        raise DomainError("对象不存在或无权访问", 404)
    return item


def canonical(source, entity, key):
    return "ob_" + hashlib.sha256(f"{source}:{entity}:{key}".encode()).hexdigest()[:24]


def preview_mapping(tx, root, mapping):
    source = required(tx, "source", mapping["source_id"])
    model = ENTITIES[mapping["entity"]]
    schema = next((t for t in discover(root, source) if t["name"] == mapping["table"]), None)
    if schema is None:
        raise DomainError("来源表不存在")
    if set(mapping["fields"]) != set(model.model_fields):
        raise DomainError("必须为全部标准字段建立映射，且不能包含未知目标字段")
    if not set(mapping["fields"].values()) <= set(schema["columns"]):
        raise DomainError("映射引用了不存在的来源字段")
    raw = read_rows(root, source, mapping["table"])
    normalized, errors, seen = [], [], set()
    for i, row in enumerate(raw):
        try:
            values = model.model_validate({k: row[v] for k, v in mapping["fields"].items()}).model_dump(
                mode="json"
            )
            if values["id"] in seen:
                raise ValueError("来源主键重复")
            seen.add(values["id"])
            normalized.append(values)
        except ValueError as exc:
            # Do not echo raw record values into logs/errors.
            errors.append({"row": i + 1, "error": str(exc).split("input_value")[0][:220]})
    return normalized, errors


def sync_source(tx, root, source_id, actor):
    source = required(tx, "source", source_id)
    mappings = [m for m in tx.all("mapping") if m["source_id"] == source_id]
    if not mappings or any(m["status"] != "confirmed" for m in mappings):
        raise DomainError("请先确认该数据源的全部字段映射")
    staged = []
    for mapping in mappings:
        rows, errors = preview_mapping(tx, root, mapping)
        if errors:
            raise DomainError(
                f"{mapping['table']} 有 {len(errors)} 条校验错误；整批未写入。首条：{errors[0]}"
            )
        staged.extend((mapping, row) for row in rows)
        if len(staged) > MAX_ROWS:
            raise DomainError("单数据源总记录超过 10,000 行，请拆分来源")
    others = {(o["entity"], o["data"]["id"]): o for o in tx.all("object") if o["source_id"] != source_id}
    seen = set()
    for mapping, row in staged:
        identity = (mapping["entity"], row["id"])
        if identity in others or identity in seen:
            raise DomainError(f"业务主键冲突：{identity[0]}/{identity[1]}。请核实来源及主键，禁止自动合并")
        seen.add(identity)
    at = now()
    modified = datetime.fromtimestamp(allowed_path(root, source["path"]).stat().st_mtime, UTC).isoformat()
    active, changed = set(), 0
    for mapping, row in staged:
        key = canonical(source_id, mapping["entity"], row["id"])
        active.add(key)
        old = tx.get("object", key)
        version = (
            old["version"]
            if old
            else max((r["version"] for r in tx.all("revision") if r["id"] == key), default=0)
        )
        is_changed = not old or old["data"] != row or old["mapping_version"] != mapping["version"]
        if is_changed:
            version += 1
            changed += 1
        obj = {
            "id": key,
            "entity": mapping["entity"],
            "data": row,
            "source_id": source_id,
            "source_name": source["name"],
            "source_key": row["id"],
            "source_table": mapping["table"],
            "mapping_id": mapping["id"],
            "mapping_version": mapping["version"],
            "version": version,
            "synced_at": at,
            "source_updated_at": modified,
        }
        tx.put("object", key, obj)
        if is_changed:
            tx.put("revision", f"{key}:{version:06}", {**obj, "event": "upsert"})
    removed = 0
    for old in tx.all("object"):
        if old["source_id"] == source_id and old["id"] not in active:
            tx.put(
                "revision",
                f"{old['id']}:{old['version'] + 1:06}",
                {**old, "version": old["version"] + 1, "event": "removed", "synced_at": at},
            )
            tx.remove("object", old["id"])
            removed += 1
    source.update(status="connected", synced_at=at, records=len(active), last_error=None)
    tx.put("source", source_id, source)
    run = {
        "id": uuid4().hex,
        "source_id": source_id,
        "source_name": source["name"],
        "at": at,
        "status": "success",
        "records": len(active),
        "changed": changed,
        "removed": removed,
        "mode": "bounded_full_snapshot",
    }
    tx.put("run", at + run["id"], run)
    tx.audit(
        actor, "sync.completed", source_id, {"records": len(active), "changed": changed, "removed": removed}
    )
    return run


class Business:
    def __init__(self, tx):
        self.tx = tx
        self.objects = tx.all("object")
        self.index = {(o["entity"], o["data"]["id"]): o for o in self.objects}
        self.by_id = {o["id"]: o for o in self.objects}
        self._schedules = {}
        self._by_entity = {entity: [o for o in self.objects if o["entity"] == entity] for entity in ENTITIES}
        self.overrides = {r["id"]: r for r in tx.all("resolution")}

    def rows(self, entity):
        return self._by_entity.get(entity, [])

    def relation(self, obj, field, entity):
        override = self.overrides.get(obj["id"] + ":" + field)
        # A source change invalidates a previously approved correction.
        if override and override["source_version"] == obj["version"]:
            target = self.by_id.get(override["target_id"])
            return target if target and target["entity"] == entity else None
        return self.index.get((entity, obj["data"][field]))

    def issues(self):
        result = []
        for entity, field, target, label, _ in RELATIONS:
            for obj in self.rows(entity):
                if not self.relation(obj, field, target):
                    result.append(
                        {
                            "id": obj["id"] + ":" + field,
                            "object_id": obj["id"],
                            "source_key": obj["source_key"],
                            "field": field,
                            "value": obj["data"][field],
                            "entity": entity,
                            "target_entity": target,
                            "label": label,
                            "reason": "来源引用缺失，需业务人员确认",
                            "candidates": [
                                {"id": t["id"], "label": t["data"]["id"]} for t in self.rows(target)
                            ],
                        }
                    )
        return result

    def order(self, order_id):
        obj = self.index.get(("SalesOrder", order_id))
        if not obj:
            raise DomainError("订单不存在", 404)
        return obj

    def work_orders(self, order):
        return [
            o
            for o in self.rows("WorkOrder")
            if (self.relation(o, "order_id", "SalesOrder") or {}).get("id") == order["id"]
        ]

    def operations(self, work):
        return [
            o
            for o in self.rows("Operation")
            if (self.relation(o, "work_order_id", "WorkOrder") or {}).get("id") == work["id"]
        ]

    def progress(self, order):
        works = self.work_orders(order)
        finished = sum(w["data"]["finished"] for w in works)
        allocated = sum(w["data"]["quantity"] for w in works)
        return {
            "quantity": order["data"]["quantity"],
            "allocated": allocated,
            "finished": finished,
            "percent": round(min(finished / order["data"]["quantity"] * 100, 100), 1),
            "allocation_valid": allocated == order["data"]["quantity"],
            "work_orders": works,
            "operations": [o for w in works for o in self.operations(w)],
        }

    def materials(self, order):
        product = self.relation(order, "product_id", "Product")
        material = self.relation(product, "material_id", "Material") if product else None
        if not material:
            return {"known": False, "reason": "产品或物料引用缺失", "shortage": None}
        inventory = [
            o
            for o in self.rows("Inventory")
            if (self.relation(o, "material_id", "Material") or {}).get("id") == material["id"]
        ]
        # Due-date priority allocates shared available inventory exactly once across open orders.
        stock = sum(o["data"]["available"] for o in inventory)
        reserved = 0
        for other in sorted(self.rows("SalesOrder"), key=lambda x: (x["data"]["due_date"], x["data"]["id"])):
            p = self.relation(other, "product_id", "Product")
            m = self.relation(p, "material_id", "Material") if p else None
            if not m or m["id"] != material["id"]:
                continue
            demand = (
                max(0, other["data"]["quantity"] - self.progress(other)["finished"])
                * p["data"]["material_per_unit"]
            )
            if other["id"] == order["id"]:
                available = max(0, stock - reserved)
                return {
                    "known": bool(inventory),
                    "material": material["data"],
                    "required": round(demand, 2),
                    "stock_total": stock,
                    "prior_allocations": round(reserved, 2),
                    "available": round(available, 2),
                    "shortage": round(max(0, demand - available), 2) if inventory else None,
                    "policy": "due_date_then_order_id_v1",
                    "inventory": inventory,
                }
            reserved += demand
        raise DomainError("订单物料分配失败")

    def schedule(self, extra_hours=0):
        """Deterministic finite-capacity heuristic, sequential operations, calendar days, no optimality claim."""
        if extra_hours in self._schedules:
            return self._schedules[extra_hours]
        loads, finish, unknown = {}, {}, set()
        for order in sorted(self.rows("SalesOrder"), key=lambda o: (o["data"]["due_date"], o["data"]["id"])):
            work_orders = self.work_orders(order)
            end_days = 0.0
            if not work_orders or not self.progress(order)["allocation_valid"]:
                unknown.add(order["id"])
            for work in work_orders:
                ops = self.operations(work)
                resource = self.relation(work, "resource_id", "Resource")
                if not resource or (work["data"]["finished"] < work["data"]["quantity"] and not ops):
                    unknown.add(order["id"])
                    continue
                rid = resource["id"]
                hours = sum(o["data"]["remaining_hours"] for o in ops)
                if work["data"]["finished"] < work["data"]["quantity"] and hours == 0:
                    unknown.add(order["id"])
                if hours:
                    loads[rid] = loads.get(rid, 0) + hours
                    end_days = max(
                        end_days, loads[rid] / min(24, resource["data"]["daily_hours"] + extra_hours)
                    )
            finish[order["id"]] = None if order["id"] in unknown else math.ceil(end_days)
        self._schedules[extra_hours] = finish
        return finish

    def risk(self, order, extra_hours=0):
        progress, material = self.progress(order), self.materials(order)
        days = self.schedule(extra_hours)[order["id"]]
        today = business_today()
        due_days = (date.fromisoformat(order["data"]["due_date"]) - today).days
        reasons = []
        unknown = days is None or not material["known"]
        if not progress["allocation_valid"]:
            reasons.append("工单分配数量与订单需求不一致")
        if days is None:
            reasons.append("工单、工序或产能数据不完整")
        if not material["known"]:
            reasons.append("物料或库存数据缺失")
        shortage = material.get("shortage") or 0
        if shortage:
            reasons.append(f"按交期分配后物料缺口 {shortage:g} {material['material']['unit']}")
        late = (
            max(0, days - due_days) if days is not None and progress["finished"] < progress["quantity"] else 0
        )
        if late:
            reasons.append(f"当前资源队列预计晚于交期 {late} 天")
        if self.issues():
            unknown = True
            reasons.append("本体存在未解决关联，整体计算输入尚不完整")
        warnings = self.warnings()
        if warnings:
            unknown = True
            reasons.append("输入包含过期数据或未生效映射")
        return {
            "order_id": order["data"]["id"],
            "level": "unknown" if unknown else "high" if shortage or late else "low",
            "reasons": reasons or ["当前规则下未发现缺料或交期冲突"],
            "production_days": days,
            "estimated_finish": (today + timedelta(days=days)).isoformat() if days is not None else None,
            "delay_days": late,
            "progress": progress["percent"],
            "material_shortage": material.get("shortage"),
            "rule_version": "delivery_risk_v1",
            "is_prediction": True,
            "assumptions": [
                "按交期优先分配共享产能和库存",
                "每日可用工时固定，按自然日估算",
                "不含在途、换线与非工作日",
                "完工量为 MES 工单累计值",
            ],
        }

    def warnings(self):
        sources = {s["id"]: s for s in self.tx.all("source")}
        warnings = set()
        for obj in self.objects:
            source = sources[obj["source_id"]]
            if source["status"] == "error":
                warnings.add(f"{source['name']} 最近同步失败，当前为历史快照")
            age = (datetime.now(UTC) - datetime.fromisoformat(obj["source_updated_at"])).total_seconds()
            if age > source["freshness_minutes"] * 60:
                warnings.add(f"{source['name']} 的来源文件已超过时效 SLA")
            mapping = self.tx.get("mapping", obj["mapping_id"])
            if (
                not mapping
                or mapping["status"] != "confirmed"
                or mapping["version"] != obj["mapping_version"]
            ):
                warnings.add(f"{source['name']} 的映射变更尚未确认并同步")
        return sorted(warnings)

    def envelope(self, result):
        # Global queues influence risk: include all contributing input objects, not only the queried order.
        return {
            "result": result,
            "meta": {
                "computed_at": now(),
                "rule_version": "delivery_risk_v1",
                "warnings": self.warnings(),
                "unresolved_relations": len(self.issues()),
                "provenance": [
                    {
                        k: o[k]
                        for k in [
                            "id",
                            "entity",
                            "source_name",
                            "source_table",
                            "source_key",
                            "version",
                            "mapping_version",
                            "synced_at",
                            "source_updated_at",
                        ]
                    }
                    for o in self.objects
                ],
            },
        }


CAPABILITIES = [
    {
        "name": "list_due_orders",
        "label": "即将交付订单",
        "description": "查询未来 N 天内到期（含逾期）的订单与风险",
        "write": False,
    },
    {
        "name": "get_order_360",
        "label": "订单全景",
        "description": "关联订单、工单、工序、资源与物料来源",
        "write": False,
    },
    {
        "name": "check_delivery_risk",
        "label": "交期风险诊断",
        "description": "基于可追溯规则分析产能与物料约束",
        "write": False,
    },
    {
        "name": "get_material_shortage",
        "label": "物料缺口",
        "description": "按交期优先分配库存，计算剩余生产需求",
        "write": False,
    },
    {
        "name": "get_production_progress",
        "label": "生产进度",
        "description": "汇总 MES 工单完工量与剩余工序",
        "write": False,
    },
    {
        "name": "simulate_schedule",
        "label": "产能情景比较",
        "description": "比较每日追加工时的交期估算；确定性启发式，非最优排程",
        "write": False,
    },
    {
        "name": "propose_schedule_change",
        "label": "创建排产草案",
        "description": "通过预检后进入人工审批，不执行 MES 写回",
        "write": True,
    },
]


def call_capability(tx, name, args, actor):
    business = Business(tx)
    if name not in {c["name"] for c in CAPABILITIES}:
        raise DomainError("业务能力不存在", 404)
    if name == "list_due_orders":
        cutoff = (business_today() + timedelta(days=args.days)).isoformat()
        result = [
            {**o["data"], "risk": business.risk(o)}
            for o in business.rows("SalesOrder")
            if o["data"]["due_date"] <= cutoff and business.progress(o)["finished"] < o["data"]["quantity"]
        ]
    elif name == "simulate_schedule":
        result = {
            "extra_hours": args.extra_hours,
            "method": "due_date_priority_v1",
            "optimal": False,
            "orders": [
                {
                    "order_id": o["data"]["id"],
                    "baseline": business.risk(o),
                    "scenario": business.risk(o, args.extra_hours),
                }
                for o in business.rows("SalesOrder")
            ],
        }
    else:
        if not args.order_id:
            raise DomainError("该能力需要 order_id")
        order = business.order(args.order_id)
        if name == "get_order_360":
            result = {
                "order": order,
                "progress": business.progress(order),
                "materials": business.materials(order),
                "risk": business.risk(order),
            }
        elif name == "check_delivery_risk":
            result = business.risk(order)
        elif name == "get_material_shortage":
            result = business.materials(order)
        elif name == "get_production_progress":
            result = business.progress(order)
        else:
            if not args.idempotency_key or len(args.note) < 3:
                raise DomainError("草案需要幂等键和至少 3 个字符的业务说明")
            payload = {"order_id": args.order_id, "extra_hours": args.extra_hours, "note": args.note}
            key = hashlib.sha256(f"{actor}:{args.idempotency_key}".encode()).hexdigest()
            old = tx.get("action_key", key)
            if old:
                if old["payload"] != payload:
                    raise DomainError("幂等键已用于不同内容", 409)
                return business.envelope(required(tx, "action", old["action_id"]))
            risk = business.risk(order)
            if risk["level"] == "unknown":
                raise DomainError("数据不完整或超过时效，排产草案预检未通过")
            result = {
                "id": "ACT-" + uuid4().hex[:8].upper(),
                **payload,
                "status": "pending_approval",
                "created_at": now(),
                "created_by": actor,
                "precheck": risk,
                "scenario": business.risk(order, args.extra_hours),
                "executed": False,
                "history": [{"at": now(), "status": "pending_approval", "actor": actor}],
                "input_versions": {o["id"]: o["version"] for o in business.objects},
                "resolution_snapshot": tx.all("resolution"),
            }
            tx.put("action", result["id"], result)
            tx.put("action_key", key, {"payload": payload, "action_id": result["id"]})
            tx.audit(actor, "action.proposed", result["id"], payload)
    tx.audit(actor, "capability.called", name, {"order_id": args.order_id})
    return business.envelope(result)
