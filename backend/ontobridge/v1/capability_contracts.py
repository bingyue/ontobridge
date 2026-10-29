from typing import Literal

from pydantic import Field, model_validator

from ..models import Strict
from .ontology_contracts import NAME

TEMPLATES = {
    "entity_lookup": "按业务身份查询对象与批准关系",
    "order_360": "订单全景、工单进度与有据关系",
    "delivery_risk": "交期风险（交期优先、共享产能与物料规则）",
    "material_shortage": "物料缺口（全局交期优先分配）",
    "production_progress": "订单生产进度",
    "list_due_orders": "未来 N 天内的待交付订单",
    "schedule_draft": "创建辅助排产草案（不写回源系统）",
}


class CapabilitySpec(Strict):
    name: str = Field(pattern=NAME)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=5, max_length=1000)
    ontology_id: str
    ontology_version: int = Field(ge=1)
    template: Literal[
        "entity_lookup",
        "order_360",
        "delivery_risk",
        "material_shortage",
        "production_progress",
        "list_due_orders",
        "schedule_draft",
    ]
    root_object: str = Field(default="SalesOrder", pattern=NAME)
    roles: list[Literal["viewer", "operator", "approver", "admin"]] = Field(
        default_factory=lambda: ["viewer", "operator", "approver", "admin"], min_length=1
    )
    max_results: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: int = Field(default=10, ge=1, le=30)
    rule_version: Literal["manufacturing_v1"] = "manufacturing_v1"

    @model_validator(mode="after")
    def business_root(self):
        if self.template != "entity_lookup" and self.root_object != "SalesOrder":
            raise ValueError("制造业模板以 SalesOrder 为根对象")
        if self.template == "schedule_draft" and "viewer" in self.roles:
            raise ValueError("只读访客不能创建动作草案")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("角色列表重复")
        return self


def input_schema(spec):
    properties, required = {}, []
    if spec["template"] == "list_due_orders":
        properties["days"] = {"type": "integer", "minimum": 0, "maximum": 365, "default": 7}
    else:
        key = "entity_id" if spec["template"] == "entity_lookup" else "order_id"
        properties[key] = {"type": "string", "minLength": 1, "maxLength": 200}
        required.append(key)
    if spec["template"] == "schedule_draft":
        properties.update(
            extra_hours={"type": "number", "minimum": 0, "maximum": 8},
            note={"type": "string", "minLength": 3, "maxLength": 2000},
            idempotency_key={"type": "string", "minLength": 8, "maxLength": 100},
        )
        required += ["extra_hours", "note", "idempotency_key"]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def output_schema(spec=None):
    entity = {
        "type": "object",
        "required": ["id", "data", "provenance"],
        "properties": {
            "id": {"type": "string"},
            "data": {"type": "object"},
            "provenance": {"type": "array", "items": {"type": "object"}},
        },
    }
    progress = {
        "type": "object",
        "required": [
            "quantity",
            "allocated",
            "finished",
            "percent",
            "allocation_valid",
            "work_orders",
            "operations",
        ],
        "properties": {
            "quantity": {"type": "integer"},
            "allocated": {"type": "integer"},
            "finished": {"type": "integer"},
            "percent": {"type": "number"},
            "allocation_valid": {"type": "boolean"},
            "work_orders": {"type": "array", "items": entity},
            "operations": {"type": "array", "items": entity},
        },
    }
    risk = {
        "type": "object",
        "required": ["level", "reasons"],
        "properties": {
            "level": {"enum": ["unknown", "low", "high"]},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
    }
    results = {
        "entity_lookup": {
            "type": "object",
            "required": ["object", "related", "relations", "truncated"],
            "properties": {
                "object": entity,
                "related": {"type": "array", "items": entity},
                "relations": {"type": "array"},
                "truncated": {"type": "boolean"},
            },
        },
        "order_360": {
            "type": "object",
            "required": ["order", "progress", "risk", "relationships"],
            "properties": {
                "order": entity,
                "progress": progress,
                "risk": risk,
                "relationships": {"type": "array"},
            },
        },
        "production_progress": progress,
        "delivery_risk": risk,
        "material_shortage": {
            "type": "object",
            "required": ["known", "shortage"],
            "properties": {"known": {"type": "boolean"}, "shortage": {"type": ["number", "null"]}},
        },
        "list_due_orders": {
            "type": "object",
            "required": ["orders", "count", "truncated"],
            "properties": {
                "orders": {"type": "array", "items": entity},
                "count": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
        },
        "schedule_draft": {
            "type": "object",
            "required": ["status", "proposal", "executed", "writeback_enabled"],
            "properties": {
                "status": {"enum": ["sandbox_preview", "pending_approval", "approved_draft", "rejected"]},
                "proposal": {"type": "object"},
                "executed": {"const": False},
                "writeback_enabled": {"const": False},
            },
        },
    }
    return {
        "type": "object",
        "required": ["result", "meta"],
        "additionalProperties": False,
        "properties": {
            "result": results.get((spec or {}).get("template"), {"type": "object"}),
            "meta": {
                "type": "object",
                "required": ["ontology_version", "capability_version", "computed_at", "provenance"],
                "properties": {
                    "ontology_version": {"type": "string"},
                    "capability_version": {"type": "string"},
                    "computed_at": {"type": "string", "format": "date-time"},
                    "provenance": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
    }
