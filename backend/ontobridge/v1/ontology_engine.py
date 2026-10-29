"""Deterministic, evidence-backed materialization of approved ontology versions."""

import hashlib
import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, uuid5

from ..service import DomainError, required
from ..store import now
from .ontology_contracts import MappingSpec, OntologySpec

# Deliberately small, explicit and dimension-checked; no eval or user-defined executable code.
UNITS = {
    "m": ("length", 1),
    "cm": ("length", 0.01),
    "mm": ("length", 0.001),
    "kg": ("mass", 1),
    "g": ("mass", 0.001),
    "h": ("time", 1),
    "min": ("time", 1 / 60),
    "s": ("time", 1 / 3600),
    "piece": ("count", 1),
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def canonical_id(tenant, ontology_id, entity, pipeline_id, key):
    return str(uuid5(NAMESPACE_URL, json.dumps([tenant, ontology_id, entity, pipeline_id, str(key)])))


def transform(value, rule):
    if value is None or value == "":
        return None
    operation = rule["transform"]
    try:
        if operation == "string":
            return str(value)
        if operation in ("integer", "number", "unit"):
            if isinstance(value, bool):
                raise ValueError()
            number = Decimal(str(value))
            if not number.is_finite():
                raise ValueError()
            if operation == "integer":
                if number != number.to_integral_value():
                    raise ValueError()
                return int(number)
            if operation == "unit":
                source, target = UNITS.get(rule["source_unit"]), UNITS.get(rule["target_unit"])
                if not source or not target or source[0] != target[0]:
                    raise ValueError()
                number = number * Decimal(str(source[1])) / Decimal(str(target[1]))
            result = float(number)
            if not math.isfinite(result):
                raise ValueError()
            return result
        if operation == "boolean":
            if value in (True, "true", "1", 1):
                return True
            if value in (False, "false", "0", 0):
                return False
            raise ValueError()
        if operation == "date":
            return date.fromisoformat(str(value)).isoformat()
        if operation == "datetime":
            result = datetime.fromisoformat(str(value))
            if result.tzinfo is None:
                raise ValueError()
            return result.astimezone(UTC).isoformat()
        if operation == "enum":
            return rule["values"][str(value)]
        return value
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as exc:
        raise DomainError("字段转换失败：" + operation) from exc


def check_value(value, attribute):
    if value is None:
        if attribute["required"]:
            raise DomainError("必填属性为空")
        return
    kind = attribute["type"]
    valid = (
        isinstance(value, str)
        if kind in ("string", "date", "datetime")
        else isinstance(value, bool)
        if kind == "boolean"
        else type(value) is int
        if kind == "integer"
        else type(value) in (int, float)
    )
    if not valid:
        raise DomainError("属性类型不匹配：" + kind)
    if kind == "date":
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise DomainError("日期格式无效") from exc
    if kind == "datetime":
        try:
            if datetime.fromisoformat(value).tzinfo is None:
                raise ValueError()
        except ValueError as exc:
            raise DomainError("时间必须包含时区") from exc
    if kind in ("number", "integer"):
        if not math.isfinite(value):
            raise DomainError("数值必须有限")
        if attribute.get("exclusive_minimum") is not None and value <= attribute["exclusive_minimum"]:
            raise DomainError("数值违反 exclusive_minimum 约束")
        for key, invalid in [
            ("minimum", lambda limit: value < limit),
            ("maximum", lambda limit: value > limit),
        ]:
            if attribute[key] is not None and invalid(attribute[key]):
                raise DomainError("数值违反 " + key + " 约束")
    if attribute["enum"] and value not in attribute["enum"]:
        raise DomainError("值不在批准的枚举范围内")


def mapping_dependencies(tx, mapping, spec):
    definition = next((o for o in spec["objects"] if o["name"] == mapping["object"]), None)
    if not definition:
        raise DomainError("映射目标对象不存在")
    pipeline = required(tx, "v1_pipeline", mapping["pipeline_id"])
    schema = required(tx, "v1_schema", pipeline["source_id"])
    table = next((t for t in schema["tables"] if t["name"] == pipeline["table"]), None)
    if not table:
        raise DomainError("来源表结构已失效，请重新发现")
    columns = {c["name"] for c in table["columns"]}
    attrs = {a["name"]: a for a in definition["attributes"]}
    if set(mapping["fields"]) - set(attrs):
        raise DomainError("映射包含目标中不存在的属性")
    if any(a["required"] and name not in mapping["fields"] for name, a in attrs.items()):
        raise DomainError("映射缺少必填属性")
    for name, rule in mapping["fields"].items():
        if rule["source"] not in columns:
            raise DomainError("映射引用了不存在的来源字段")
        if rule["transform"] == "unit" and rule["target_unit"] != attrs[name]["unit"]:
            raise DomainError("转换目标单位与本体属性单位不一致")
    return definition, pipeline


def build_snapshot(tx, ontology_id, spec, mappings, identities=None):
    """Pure draft evaluation; caller chooses whether to persist this version atomically."""
    OntologySpec.model_validate(spec)
    issues, entities, aliases, relationships = [], {}, [], []
    maps = {m["id"]: m for m in mappings}
    identities = identities or []
    decisions = {(d["mapping_id"], d["source_key"]): d for d in identities}
    by_object = {o["name"]: [] for o in spec["objects"]}
    assets = tx.all("v1_asset")
    raw_by_mapping = {}

    def issue(code, message, refs=None, severity="error"):
        issues.append({"code": code, "severity": severity, "message": message, "evidence": refs or []})

    for mapping_id in spec["mapping_ids"]:
        mapping = maps.get(mapping_id)
        if not mapping or mapping["status"] != "approved" or mapping["ontology_id"] != ontology_id:
            issue("unapproved_mapping", "引用的映射不存在、属于其他本体或未批准", [mapping_id])
            continue
        try:
            MappingSpec.model_validate({k: mapping[k] for k in MappingSpec.model_fields})
            definition, pipeline = mapping_dependencies(tx, mapping, spec)
        except DomainError as exc:
            issue("mapping_dependency", exc.message, [mapping_id])
            continue
        rows = [a for a in assets if a["pipeline_id"] == pipeline["id"]]
        raw_by_mapping[mapping_id] = {a["source_key"]: a for a in rows}
        if not pipeline.get("last_success_at"):
            issue("not_synced", "流水线尚未成功同步", [pipeline["id"]])
        if pipeline.get("last_error"):
            issue("source_error", "来源最近一次同步失败", [pipeline["id"]], "warning")
        seen = {}
        for asset in rows:
            data = {}
            try:
                for attribute in definition["attributes"]:
                    rule = mapping["fields"].get(attribute["name"])
                    value = transform(asset["data"].get(rule["source"]), rule) if rule else None
                    check_value(value, attribute)
                    data[attribute["name"]] = value
                comparisons = {
                    "lte": lambda a, b: a <= b,
                    "lt": lambda a, b: a < b,
                    "gte": lambda a, b: a >= b,
                    "gt": lambda a, b: a > b,
                    "eq": lambda a, b: a == b,
                }
                for constraint in definition.get("constraints", []):
                    left, right = data[constraint["left"]], data[constraint["right"]]
                    if left is None or right is None or not comparisons[constraint["operator"]](left, right):
                        raise DomainError(constraint["message"])
            except DomainError as exc:
                issue("attribute_constraint", exc.message, [mapping_id, asset["id"]])
                continue
            key = str(data[definition["identity"]])
            if key in seen:
                issue(
                    "duplicate_identity",
                    "同一映射中业务身份重复，必须修正来源或映射",
                    [seen[key], asset["id"]],
                )
                continue
            seen[key] = asset["id"]
            cid = canonical_id(tx.tenant, ontology_id, mapping["object"], pipeline["id"], key)
            record = {
                "id": cid,
                "entity_type": mapping["object"],
                "data": data,
                "provenance": [
                    {
                        "asset_id": asset["id"],
                        "source_id": asset["source_id"],
                        "source_key": asset["source_key"],
                        "pipeline_id": pipeline["id"],
                        "mapping_id": mapping_id,
                        "mapping_version": mapping["version"],
                        "run_id": asset["run_id"],
                        "source_updated_at": asset["source_updated_at"],
                        "synced_at": asset["synced_at"],
                        "digest": asset["digest"],
                    }
                ],
            }
            if cid in entities:
                issue("duplicate_mapping", "同一来源身份被多个映射重复定义", [mapping_id, cid])
                continue
            entities[cid] = record
            aliases.append(
                {
                    "canonical_id": cid,
                    "entity_type": mapping["object"],
                    "source_id": asset["source_id"],
                    "source_primary_key": asset["source_key"],
                    "pipeline_id": pipeline["id"],
                    "mapping_id": mapping_id,
                    "mapped_at": now(),
                    "business_key": key,
                }
            )

    # Explicit, approved, one-hop merges only. Never merge similar names or equal product codes.
    alias_index = {(a["mapping_id"], a["source_primary_key"]): a for a in aliases}
    for pair, decision in decisions.items():
        source = alias_index.get(pair)
        target_pair = (decision["target_mapping_id"], decision["target_source_key"])
        target = alias_index.get(target_pair)
        if (
            not source
            or not target
            or source["entity_type"] != target["entity_type"]
            or target_pair in decisions
        ):
            issue("invalid_identity_decision", "身份复核目标失效、类型不同或形成链/环", [decision["id"]])
            continue
        sid, tid = source["canonical_id"], target["canonical_id"]
        if sid == tid:
            issue("invalid_identity_decision", "不能合并同一个身份", [decision["id"]])
            continue
        original = entities.pop(sid, None)
        if original is None or tid not in entities:
            issue("invalid_identity_decision", "重复或冲突的身份合并", [decision["id"]])
            continue
        # The human selected target is authoritative; differing fields remain visible as evidence.
        winner = entities[tid]
        differences = {
            k: {"source": v, "target": winner["data"].get(k)}
            for k, v in original["data"].items()
            if winner["data"].get(k) != v
        }
        winner.setdefault("identity_resolutions", []).append(
            {"decision_id": decision["id"], "reason": decision["reason"], "differences": differences}
        )
        winner["provenance"].extend(original["provenance"])
        source["canonical_id"] = tid
        source["decision_id"] = decision["id"]
    for entity in entities.values():
        by_object[entity["entity_type"]].append(entity)
    for obj in spec["objects"]:
        if not any(m.get("object") == obj["name"] and m["id"] in spec["mapping_ids"] for m in mappings):
            issue("unmapped_object", "对象尚无批准映射：" + obj["name"])
        grouped = {}
        for entity in by_object[obj["name"]]:
            grouped.setdefault(str(entity["data"][obj["identity"]]), []).append(entity["id"])
        for ids in grouped.values():
            if len(ids) > 1:
                issue("identity_conflict", "多个来源存在相同业务身份，需要人工确认或修正", ids)

    for relation in spec["relations"]:
        left, right = by_object[relation["from_object"]], by_object[relation["to_object"]]
        left_index, right_index = {}, {}
        for row in left:
            if row["data"][relation["from_field"]] is not None:
                left_index.setdefault(row["data"][relation["from_field"]], []).append(row)
        for row in right:
            if row["data"][relation["to_field"]] is not None:
                right_index.setdefault(row["data"][relation["to_field"]], []).append(row)
        used = set()
        if relation["via_object"]:
            candidates = [
                (
                    r,
                    left_index.get(r["data"][relation["via_from_field"]], []),
                    right_index.get(r["data"][relation["via_to_field"]], []),
                )
                for r in by_object[relation["via_object"]]
            ]
        else:
            candidates = [(r, [r], right_index.get(r["data"][relation["from_field"]], [])) for r in left]
        for evidence, from_rows, to_rows in candidates:
            if len(from_rows) != 1 or len(to_rows) != 1:
                if relation["required"] or len(from_rows) > 1 or len(to_rows) > 1:
                    issue(
                        "ambiguous_relation" if len(from_rows) > 1 or len(to_rows) > 1 else "orphan_relation",
                        "关系无法唯一解析：" + relation["name"],
                        [evidence["id"]],
                    )
                continue
            source, target = from_rows[0], to_rows[0]
            if relation["cardinality"] == "one_to_one" and target["id"] in used:
                issue("cardinality_violation", "一对一关系出现多个来源引用", [source["id"], target["id"]])
                continue
            used.add(target["id"])
            relationships.append(
                {
                    "id": digest([relation["name"], source["id"], target["id"], evidence["id"]]),
                    "from_canonical_id": source["id"],
                    "to_canonical_id": target["id"],
                    "relation_type": relation["name"],
                    "evidence_ref": evidence["provenance"],
                    "rule": {
                        "from_field": relation["from_field"],
                        "to_field": relation["to_field"],
                        "evidence": relation["evidence"],
                    },
                }
            )
    metrics = {}
    for rule in spec["metrics"]:
        rows = by_object[rule["object"]]
        numerator = sum(r["data"].get(rule["numerator"]) or 0 for r in rows) if rule["numerator"] else 0
        denominator = sum(r["data"].get(rule["denominator"]) or 0 for r in rows) if rule["denominator"] else 0
        value = (
            len(rows)
            if rule["operation"] == "count"
            else numerator
            if rule["operation"] == "sum"
            else (numerator / denominator if denominator else None)
        )
        metrics[rule["name"]] = {
            "value": value,
            "unit": rule["unit"],
            "rule": rule,
            "undefined_reason": "分母为零" if rule["operation"] == "ratio" and not denominator else None,
        }
    return {
        "built_at": now(),
        "entities": list(entities.values()),
        "aliases": aliases,
        "relationships": relationships,
        "metrics": metrics,
        "issues": issues,
        "valid": not any(i["severity"] == "error" for i in issues),
        "counts": {"entities": len(entities), "aliases": len(aliases), "relationships": len(relationships)},
    }
