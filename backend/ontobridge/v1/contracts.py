from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from ..models import Strict

Identifier = str


class SourceSpec(Strict):
    name: str = Field(min_length=1, max_length=100)
    system: Literal["ERP", "MES", "WMS", "OTHER"]
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    path: str | None = Field(default=None, max_length=500)
    connection_ref: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    schema_name: str | None = Field(default=None, max_length=100)
    tables: list[str] = Field(default_factory=list, max_length=100)
    freshness_minutes: int = Field(default=1440, ge=1, le=525600)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def connection_required(self):
        if self.kind in ("csv", "excel", "sqlite") and not self.path:
            raise ValueError("文件连接器需要授权目录内的 path")
        if self.kind in ("postgresql", "mysql", "mssql", "sftp", "web_rpa") and not self.connection_ref:
            raise ValueError("远程来源必须引用管理员配置的 connection_ref")
        return self


class CursorSpec(Strict):
    column: str = Field(min_length=1, max_length=100)
    tie_breaker: str = Field(min_length=1, max_length=100)
    kind: Literal["integer", "datetime", "string"] = "datetime"

    @model_validator(mode="after")
    def different(self):
        if self.column == self.tie_breaker:
            raise ValueError("增量字段和唯一主键应为两个字段")
        return self


class PipelineSpec(Strict):
    name: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1)
    table: str = Field(min_length=1, max_length=100)
    mode: Literal["full", "incremental"] = "full"
    cursor: CursorSpec | None = None
    batch_size: int = Field(default=500, ge=1, le=5000)
    poll_seconds: int = Field(default=0, ge=0, le=86400)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_seconds: int = Field(default=5, ge=1, le=3600)
    primary_key: str = Field(min_length=1, max_length=100)
    enabled: bool = True

    @model_validator(mode="after")
    def cursor_required(self):
        if self.mode == "incremental" and self.cursor is None:
            raise ValueError("增量同步必须指定游标和唯一主键")
        if self.cursor and self.cursor.tie_breaker != self.primary_key:
            raise ValueError("游标 tie_breaker 必须使用流水线主键")
        if 0 < self.poll_seconds < 5:
            raise ValueError("轮询间隔至少 5 秒")
        return self


class SourceColumn(Strict):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    primary_key_candidate: bool = False


class TableSchema(Strict):
    name: str
    columns: list[SourceColumn]
    primary_key: list[str] = Field(default_factory=list)
    sample: list[dict[str, Any]] = Field(default_factory=list)


class ReadBatch(Strict):
    rows: list[dict[str, Any]]
    checkpoint: dict[str, Any] | None = None
    exhausted: bool
    source_updated_at: datetime
    freshness_basis: str
