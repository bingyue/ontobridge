"""Principal-scoped row/column policy; restricted users have no raw-management escape hatch."""

from typing import Literal

from pydantic import Field, model_validator

from ..models import Strict


class ObjectPolicy(Strict):
    rows: dict[str, list[str | int | float | bool | None]] = Field(default_factory=dict)
    columns: list[str] | Literal["*"] = Field(default_factory=list)
    provenance: bool = False

    @model_validator(mode="after")
    def bounded(self):
        if len(self.rows) > 100 or any(len(v) > 1000 for v in self.rows.values()):
            raise ValueError("权限规则超过配置上限")
        return self


class DataPolicy(Strict):
    objects: dict[str, ObjectPolicy] = Field(default_factory=dict)


def restricted_path_allowed(path, method):
    if method == "GET" and path in (
        "/api/me",
        "/api/v1/tools",
        "/api/v1/capabilities",
        "/api/v1/generated/openapi.json",
    ):
        return True
    return method == "POST" and (path.startswith("/api/v1/invoke/") or path.startswith("/api/v1/mcp/invoke/"))
