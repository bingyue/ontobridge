"""Local, human-operated browser recorder producing a reviewable WorkflowSpec (never auto-approved)."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from .connector_sdk import ConnectorContext
from .rpa import WorkflowSpec, allowed_request

# Only selector/action metadata is recorded. Passwords are replaced by secret references.
RECORDER_SCRIPT = r"""
(() => {
  const selector = el => {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.dataset.testid) return '[data-testid=' + JSON.stringify(el.dataset.testid) + ']';
    if (el.name) return el.tagName.toLowerCase() + '[name=' + JSON.stringify(el.name) + ']';
    let parts = [];
    while(el && el.nodeType === 1 && parts.length < 8) {
      const peers = el.parentElement ? [...el.parentElement.children].filter(n => n.tagName === el.tagName) : [el];
      parts.unshift(el.tagName.toLowerCase() + ':nth-of-type(' + (peers.indexOf(el) + 1) + ')');
      el = el.parentElement;
    }
    return parts.join(' > ');
  };
  document.addEventListener('change', event => {
    const el = event.target;
    if (!el.matches('input,textarea')) return;
    window.__ontoRecord({action:'fill', selector:selector(el), ...(el.type === 'password' ? {secret_ref: el.name || 'password'} : {value:el.value})});
  }, true);
  document.addEventListener('click', event => {
    const el = event.target.closest('button,a,input[type=submit]');
    if(el) window.__ontoRecord({action:'click',selector:selector(el)});
  }, true);
})();
"""


class Recording:
    def __init__(self, path):
        self.steps = [{"action": "navigate", "path": path}]
        self.downloaded = []

    def record(self, _, value):
        if len(self.steps) >= 39:
            return
        if (
            value["action"] == "fill"
            and self.steps
            and self.steps[-1].get("selector") == value.get("selector")
            and self.steps[-1]["action"] == "fill"
        ):
            self.steps[-1] = value
        else:
            self.steps.append(value)

    def download(self, event):
        if self.steps and self.steps[-1]["action"] == "click":
            self.steps[-1]["action"] = "download"
            self.downloaded.append(Path(event.suggested_filename).suffix.removeprefix("."))

    def attach(self, context, page):
        context.expose_binding("__ontoRecord", self.record)
        context.add_init_script(RECORDER_SCRIPT)
        page.on("download", self.download)


def main():
    parser = argparse.ArgumentParser(description="Human-operated, allowlisted ERP report export recorder")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--columns", required=True, help="Comma-separated expected report columns")
    parser.add_argument("--name", default="录制的网页报表导出")
    parser.add_argument("--path", default="/")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        parser.error("输出已存在，拒绝覆盖")
    config = ConnectorContext(
        Path("."), args.tenant, {"kind": "web_rpa", "connection_ref": args.connection}
    ).profile()
    recording = Recording(args.path)
    steps, downloaded = recording.steps, recording.downloaded
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(
            accept_downloads=True, storage_state=config.get("storage_state"), service_workers="block"
        )
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if allowed_request(route.request.url, route.request.method, config)
                else route.abort()
            ),
        )

        page = context.new_page()
        recording.attach(context, page)
        url = config["base_url"].rstrip("/") + args.path
        if not allowed_request(url, "GET", config):
            parser.error("初始路径不在管理员授权范围")
        page.goto(url)
        print("请在浏览器完成授权查询和报表下载。下载完成后关闭窗口；录制不捕获密码，不自动批准。")
        try:
            while not page.is_closed() and not downloaded:
                page.wait_for_timeout(200)
            if downloaded:
                page.wait_for_timeout(200)
        finally:
            browser.close()
    if not downloaded:
        raise SystemExit("未完成报表下载，未生成可执行流程。")
    workflow = WorkflowSpec.model_validate(
        {
            "name": args.name,
            "connection_ref": args.connection,
            "steps": steps,
            "expected_columns": [c.strip() for c in args.columns.split(",")],
            "output_format": downloaded[-1],
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as file:
        json.dump(workflow.model_dump(), file, ensure_ascii=False, indent=2)
    output.chmod(0o600)
    print("录制定义已保存。请导入工作台并由另一位审批人核准。")
