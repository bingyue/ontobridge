from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class SalesOrder(Strict):
    id: str = Field(min_length=1, max_length=100)
    customer: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    due_date: date
    unit_price: float = Field(ge=0)


class Product(Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    material_per_unit: float = Field(gt=0)


class WorkOrder(Strict):
    id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    finished: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_finished(self):
        if self.finished > self.quantity:
            raise ValueError("完工数量不能超过工单数量")
        return self


class Operation(Strict):
    id: str = Field(min_length=1)
    work_order_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    remaining_hours: float = Field(ge=0)


class Resource(Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    daily_hours: float = Field(gt=0, le=24)


class Material(Strict):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)


class Inventory(Strict):
    id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    warehouse: str = Field(min_length=1)
    available: float = Field(ge=0)


ENTITIES = {c.__name__: c for c in [SalesOrder, Product, WorkOrder, Operation, Resource, Material, Inventory]}
LABELS = dict(
    zip(ENTITIES, ["销售订单", "产品款式", "生产工单", "工序", "生产资源", "物料", "库存余额"], strict=True)
)
RELATIONS = [
    ("SalesOrder", "product_id", "Product", "订单产品", "N:1"),
    ("Product", "material_id", "Material", "物料需求", "N:1"),
    ("WorkOrder", "order_id", "SalesOrder", "订单拆单", "N:1"),
    ("WorkOrder", "resource_id", "Resource", "生产资源", "N:1"),
    ("Operation", "work_order_id", "WorkOrder", "工艺路线", "N:1"),
    ("Inventory", "material_id", "Material", "库存物料", "N:1"),
]


class SourceInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    system: Literal["ERP", "MES", "WMS", "OTHER"]
    kind: Literal["sqlite", "csv"]
    path: str = Field(min_length=1, max_length=500)
    freshness_minutes: int = Field(default=1440, ge=1, le=525600)


class MappingInput(Strict):
    source_id: str
    table: str = Field(min_length=1, max_length=100)
    entity: Literal["SalesOrder", "Product", "WorkOrder", "Operation", "Resource", "Material", "Inventory"]
    fields: dict[str, str]


class CapabilityArgs(Strict):
    order_id: str | None = Field(default=None, max_length=100)
    days: int = Field(default=7, ge=0, le=365)
    extra_hours: float = Field(default=0, ge=0, le=8)
    note: str = Field(default="", max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100)


class Decision(Strict):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=1000)


class Resolution(Strict):
    target_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)
