"""Restricted browser export workflows. No eval, arbitrary scripts, filesystem paths or source writes."""

import fnmatch
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from playwright.sync_api import sync_playwright
from pydantic import Field, model_validator

from ..models import Strict
from ..service import DomainError
from .file_connectors import MAX_BYTES, FileConnector


class RPAStep(Strict):
    action: Literal["navigate", "fill", "click", "wait", "download"]
    selector: str | None = Field(default=None, max_length=500)
    path: str | None = Field(default=None, max_length=500)
    value: str | None = Field(default=None, max_length=2000)
    secret_ref: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "navigate" and (
            not self.path or not self.path.startswith("/") or self.path.startswith("//")
        ):
            raise ValueError("导航必须为授权站点内的绝对路径")
        if self.action != "navigate" and not self.selector:
            raise ValueError("操作需要明确的元素选择器")
        if self.action == "fill" and ((self.value is None) == (self.secret_ref is None)):
            raise ValueError("填写操作必须提供 value 或 secret_ref 中的一项")
        return self


class WorkflowSpec(Strict):
    name: str = Field(min_length=1, max_length=100)
    connection_ref: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    steps: list[RPAStep] = Field(min_length=2, max_length=40)
    expected_columns: list[str] = Field(min_length=1, max_length=100)
    output_format: Literal["csv", "xlsx"] = "csv"

    @model_validator(mode="after")
    def validate_flow(self):
        if self.steps[0].action != "navigate" or self.steps[-1].action != "download":
            raise ValueError("流程必须以导航开始，以一次下载结束")
        if sum(step.action == "download" for step in self.steps) != 1:
            raise ValueError("每个流程恰好包含一个报表下载步骤")
        return self


def fingerprint(workflow):
    return hashlib.sha256(json.dumps(workflow, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def allowed_request(url, method, config):
    target = urlsplit(url)
    base = urlsplit(config["base_url"])
    if target.scheme not in ("http", "https") or target.netloc != base.netloc or target.scheme != base.scheme:
        return False
    if method in ("GET", "HEAD"):
        return any(fnmatch.fnmatchcase(target.path, pattern) for pattern in config["allowed_read_paths"])
    return method == "POST" and target.path in config.get("allowed_login_paths", [])


def prepare_source(tx, source):
    source = dict(source)
    if source["kind"] == "web_rpa":
        workflow = tx.get("v1_rpa_workflow", source["options"].get("workflow_id", ""))
        if (
            not workflow
            or workflow["status"] != "approved"
            or workflow["connection_ref"] != source["connection_ref"]
        ):
            raise DomainError("网页连接器需要当前租户已批准且连接配置匹配的录制流程", 403)
        source["_approved_workflow"] = workflow
    return source


class WebRPAConnector(FileConnector):
    kinds = ("web_rpa",)

    def content(self):
        workflow = self.context.source.get("_approved_workflow")
        if not workflow or workflow.get("status") != "approved":
            raise DomainError("未批准的网页流程不能执行", 403)
        validated = WorkflowSpec.model_validate({k: workflow[k] for k in WorkflowSpec.model_fields})
        config = self.context.profile()
        run_id = self.context.source.get("_run_id") or "preview-" + uuid4().hex
        export_root = Path(os.environ.get("ONTOBRIDGE_CACHE_ROOT", str(self.context.root))).resolve()
        tenant_root = (export_root / self.context.tenant).resolve()
        folder = (tenant_root / ".exports" / run_id).resolve()
        if not tenant_root.is_relative_to(export_root) or not folder.is_relative_to(tenant_root):
            raise DomainError("非法导出路径")
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = folder / ("export." + validated.output_format)
        receipt = folder / "receipt.json"
        digest = fingerprint(validated.model_dump())
        if destination.exists() and receipt.exists():
            metadata = json.loads(receipt.read_text())
            if (
                metadata["workflow_digest"] != digest
                or hashlib.sha256(destination.read_bytes()).hexdigest() != metadata["sha256"]
            ):
                raise DomainError("已采集报表指纹或审批版本发生变化", 409)
        else:
            self.export(config, validated, destination)
            content = destination.read_bytes()
            receipt.write_text(
                json.dumps({"workflow_digest": digest, "sha256": hashlib.sha256(content).hexdigest()})
            )
            os.chmod(receipt, 0o600)
        from datetime import UTC, datetime

        from .file_connectors import load_tables

        before = destination.stat()
        content = destination.read_bytes()
        after = destination.stat()
        if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
            raise DomainError("导出报表在读取时发生变化", 409)
        tables = load_tables(content, destination.name)
        if not tables or any(
            not set(validated.expected_columns) <= set(header) for header, _ in tables.values()
        ):
            raise DomainError("导出报表缺少预期字段，禁止采集")
        return content, destination.name, datetime.fromtimestamp(after.st_mtime, UTC)

    def export(self, config, workflow, destination: Path):
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    accept_downloads=True, service_workers="block", storage_state=config.get("storage_state")
                )
                context.set_default_timeout(15000)
                denied = []

                def intercept(route):
                    if allowed_request(route.request.url, route.request.method, config):
                        route.continue_()
                    else:
                        denied.append(urlsplit(route.request.url).path)
                        route.abort()

                context.route("**/*", intercept)
                page = context.new_page()
                try:
                    for step in workflow.steps:
                        if step.action == "navigate":
                            url = urljoin(config["base_url"], step.path)
                            if not allowed_request(url, "GET", config):
                                raise DomainError("导航超出管理员批准的只读页面范围", 403)
                            page.goto(url, wait_until="domcontentloaded")
                        elif step.action == "fill":
                            value = (
                                step.value
                                if step.secret_ref is None
                                else config.get("secrets", {}).get(step.secret_ref)
                            )
                            if value is None:
                                raise DomainError("所需认证引用未配置")
                            page.locator(step.selector).fill(value)
                        elif step.action == "click":
                            page.locator(step.selector).click()
                        elif step.action == "wait":
                            page.locator(step.selector).wait_for(state="visible")
                        else:
                            with page.expect_download(timeout=15000) as event:
                                page.locator(step.selector).click()
                            download = event.value
                            if download.failure():
                                raise DomainError("报表下载失败")
                            if (
                                Path(download.suggested_filename).suffix.lower()
                                != "." + workflow.output_format
                            ):
                                raise DomainError("下载文件类型与审批流程不一致")
                            temporary = Path(download.path())
                            if temporary.stat().st_size > MAX_BYTES:
                                raise DomainError("下载报表超过大小限制")
                            download.save_as(destination)
                            os.chmod(destination, 0o600)
                    if denied:
                        destination.unlink(missing_ok=True)
                        raise DomainError("流程尝试访问未授权页面或发出非登录写请求，已阻断", 403)
                finally:
                    context.close()
                    browser.close()
        except DomainError:
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise DomainError("网页导出失败；请检查登录状态、元素定位和授权范围", 502) from exc
