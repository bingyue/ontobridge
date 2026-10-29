"""Connector SPI. Plugins are installed by operators, never uploaded as executable code via API."""

import importlib.metadata
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..service import DomainError
from .contracts import ReadBatch, TableSchema


@dataclass(frozen=True)
class ConnectorContext:
    root: Path
    tenant: str
    source: dict
    profiles: dict | None = None

    def profile(self) -> dict:
        profiles = self.profiles
        if profiles is None:
            file = os.environ.get("ONTOBRIDGE_CONNECTIONS_FILE")
            if not file:
                raise DomainError("未配置管理员连接凭据文件", 503)
            path = Path(file)
            if path.stat().st_mode & 0o077:
                raise DomainError("连接凭据文件必须仅允许所有者访问（0600）", 503)
            profiles = json.loads(path.read_text())
        profile = profiles.get(self.tenant, {}).get(self.source.get("connection_ref"))
        if not profile or profile.get("kind") != self.source["kind"]:
            raise DomainError("连接配置不存在、类型不匹配或不属于当前租户", 422)
        return profile

    def path(self, relative=None):
        # V1 file sources live in an explicit tenant subdirectory, distinct from the V0 demo root.
        root = (self.root / self.tenant).resolve()
        if not root.is_relative_to(self.root.resolve()):
            raise DomainError("非法租户目录")
        path = (root / (relative if relative is not None else self.source["path"])).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise DomainError("来源文件不存在或不在当前租户授权目录中")
        return path


def json_value(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"binary_length": len(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def timestamp():
    return datetime.now(UTC)


class Connector(ABC):
    kinds: tuple[str, ...] = ()
    incremental: bool = False

    def __init__(self, context: ConnectorContext):
        self.context = context

    def test(self):
        tables = self.discover()
        return {"ok": True, "tables": len(tables), "read_only": True, "kind": self.context.source["kind"]}

    @abstractmethod
    def discover(self) -> list[TableSchema]: ...

    @abstractmethod
    def read(
        self,
        table: str,
        primary_key: str,
        batch_size: int,
        cursor: dict | None = None,
        checkpoint: dict | None = None,
    ) -> ReadBatch: ...


class ConnectorRegistry:
    def __init__(self):
        self._plugins: dict[str, type[Connector]] = {}

    def register(self, plugin: type[Connector]):
        if not issubclass(plugin, Connector) or not plugin.kinds:
            raise ValueError("插件必须实现 Connector 并声明 kinds")
        for kind in plugin.kinds:
            if kind in self._plugins:
                raise ValueError(f"连接器已注册：{kind}")
            self._plugins[kind] = plugin

    def load_entry_points(self):
        for entry in importlib.metadata.entry_points(group="ontobridge.connectors"):
            self.register(entry.load())

    def create(self, context: ConnectorContext):
        plugin = self._plugins.get(context.source["kind"])
        if not plugin:
            raise DomainError("连接器未安装", 422)
        return plugin(context)

    def manifest(self):
        return [
            {
                "kind": kind,
                "plugin": plugin.__name__,
                "incremental": plugin.incremental,
                "contract_version": "1.0",
                "read_only": True,
            }
            for kind, plugin in sorted(self._plugins.items())
        ]
