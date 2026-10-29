"""Versioned ontology contracts. No executable expressions or generated SQL."""

from typing import Literal

from pydantic import Field, model_validator

from ..models import Strict

NAME = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"


class Attribute(Strict):
    name: str = Field(pattern=NAME)
    label: str = Field(default="", max_length=100)
    type: Literal["string", "integer", "number", "boolean", "date", "datetime"] = "string"
    required: bool = True
    enum: list[str] = Field(default_factory=list, max_length=100)
    minimum: float | None = None
    exclusive_minimum: float | None = None
    maximum: float | None = None
    unit: str | None = Field(default=None, max_length=30)


class ObjectConstraint(Strict):
    left: str = Field(pattern=NAME)
    operator: Literal["lte", "lt", "gte", "gt", "eq"]
    right: str = Field(pattern=NAME)
    message: str = Field(min_length=3, max_length=300)


class ObjectType(Strict):
    name: str = Field(pattern=NAME)
    label: str = Field(min_length=1, max_length=100)
    identity: str = Field(pattern=NAME)
    attributes: list[Attribute] = Field(min_length=1, max_length=100)
    constraints: list[ObjectConstraint] = Field(default_factory=list, max_length=100)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})

    @model_validator(mode="after")
    def check(self):
        attrs = {a.name: a for a in self.attributes}
        if len(attrs) != len(self.attributes) or self.identity not in attrs:
            raise ValueError("属性名必须唯一且必须包含 identity")
        if not attrs[self.identity].required or attrs[self.identity].type not in ("string", "integer"):
            raise ValueError("identity 必须是必填的文本或整数属性")
        for rule in self.constraints:
            if rule.left not in attrs or rule.right not in attrs:
                raise ValueError("对象约束引用不存在的属性")
            if rule.operator != "eq" and any(
                attrs[k].type not in ("integer", "number") for k in (rule.left, rule.right)
            ):
                raise ValueError("大小比较约束必须引用数值属性")
        if set(self.position) != {"x", "y"}:
            raise ValueError("画布位置只允许 x/y")
        return self


class RelationType(Strict):
    name: str = Field(pattern=NAME)
    label: str = Field(min_length=1, max_length=100)
    from_object: str = Field(pattern=NAME)
    to_object: str = Field(pattern=NAME)
    from_field: str = Field(pattern=NAME)
    to_field: str = Field(pattern=NAME)
    cardinality: Literal["many_to_one", "one_to_one", "many_to_many"] = "many_to_one"
    required: bool = True
    evidence: str = Field(min_length=5, max_length=1000)
    # Many-to-many relations require a real junction entity with two reference attributes.
    via_object: str | None = None
    via_from_field: str | None = None
    via_to_field: str | None = None

    @model_validator(mode="after")
    def bridge(self):
        bridge = (self.via_object, self.via_from_field, self.via_to_field)
        if self.cardinality == "many_to_many" and not all(bridge):
            raise ValueError("多对多关系必须提供真实的关联单据对象及两端引用字段")
        if self.cardinality != "many_to_many" and any(bridge):
            raise ValueError("只有多对多关系允许 via 字段")
        return self


class MetricRule(Strict):
    name: str = Field(pattern=NAME)
    object: str = Field(pattern=NAME)
    operation: Literal["sum", "count", "ratio"]
    numerator: str | None = None
    denominator: str | None = None
    unit: str = Field(default="", max_length=30)
    description: str = Field(min_length=1, max_length=1000)


class OntologySpec(Strict):
    name: str = Field(min_length=1, max_length=100)
    objects: list[ObjectType] = Field(min_length=1, max_length=50)
    relations: list[RelationType] = Field(default_factory=list, max_length=100)
    metrics: list[MetricRule] = Field(default_factory=list, max_length=100)
    mapping_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def references(self):
        objects = {o.name: {a.name: a for a in o.attributes} for o in self.objects}
        if len(objects) != len(self.objects):
            raise ValueError("对象名重复")
        for records in (self.relations, self.metrics):
            if len({r.name for r in records}) != len(records):
                raise ValueError("关系或指标名称重复")
        if len(set(self.mapping_ids)) != len(self.mapping_ids):
            raise ValueError("映射引用重复")
        for r in self.relations:
            for obj, field in [(r.from_object, r.from_field), (r.to_object, r.to_field)]:
                if field not in objects.get(obj, {}):
                    raise ValueError("关系引用了不存在的对象属性")
            if objects[r.from_object][r.from_field].type != objects[r.to_object][r.to_field].type:
                raise ValueError("关系两端属性类型必须一致")
            if r.via_object:
                for field, target in [
                    (r.via_from_field, objects[r.from_object][r.from_field]),
                    (r.via_to_field, objects[r.to_object][r.to_field]),
                ]:
                    attr = objects.get(r.via_object, {}).get(field)
                    if not attr or attr.type != target.type:
                        raise ValueError("关联单据的引用属性不存在或类型不一致")
        for metric in self.metrics:
            attrs = objects.get(metric.object)
            if not attrs:
                raise ValueError("指标对象不存在")
            fields = [] if metric.operation == "count" else [metric.numerator]
            if metric.operation == "ratio":
                fields.append(metric.denominator)
            if any(f not in attrs or attrs[f].type not in ("integer", "number") for f in fields):
                raise ValueError("指标必须引用数值属性")
        return self


class FieldRule(Strict):
    source: str = Field(min_length=1, max_length=100)
    transform: Literal[
        "identity", "string", "integer", "number", "boolean", "date", "datetime", "enum", "unit"
    ] = "identity"
    values: dict[str, str] = Field(default_factory=dict)
    source_unit: str | None = None
    target_unit: str | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.transform == "enum" and not self.values:
            raise ValueError("枚举转换必须显式提供状态字典")
        if self.transform == "unit" and not (self.source_unit and self.target_unit):
            raise ValueError("单位转换必须提供来源单位和目标单位")
        return self


class MappingSpec(Strict):
    ontology_id: str
    object: str = Field(pattern=NAME)
    pipeline_id: str
    fields: dict[str, FieldRule]
    explanation: str = Field(min_length=3, max_length=4000)


class SuggestionRequest(Strict):
    ontology_id: str
    object: str
    pipeline_id: str


class RevisionRequest(Strict):
    revision: int = Field(ge=1)


class ReasonRequest(Strict):
    reason: str = Field(min_length=3, max_length=1000)


class RollbackRequest(ReasonRequest):
    version: int = Field(ge=1)


class IdentityDecision(Strict):
    ontology_id: str
    mapping_id: str
    source_key: str = Field(min_length=1, max_length=500)
    target_mapping_id: str
    target_source_key: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=5, max_length=1000)
