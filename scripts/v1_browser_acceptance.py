"""Real-browser regression for the private-mode login shell and three V1 workspaces.
Uses only the synthetic container acceptance project's credentials; never prints tokens.
"""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    state = ROOT / "var/container-acceptance"
    manifest = json.loads((state / "fixture.json").read_text())
    assert manifest["synthetic_fixture"] == "ontobridge-manufacturing-v1"
    credentials = json.loads((state / "access.json").read_text())
    receipt = json.loads((state / "receipt.json").read_text())
    assert receipt["mock"] and receipt["api_url"] == "http://127.0.0.1:18787"
    output = ROOT / "output/playwright"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for role in ["operator", "viewer"]:
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            failures = []
            page.on("pageerror", lambda error, failures=failures: failures.append(str(error)))
            page.goto(receipt["api_url"])
            page.get_by_label("企业访问令牌").fill(credentials[role])
            page.get_by_role("button", name="进入工作台").click()
            expect(page.get_by_role("heading", name="业务能力工厂", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="工作总览", exact=True)).to_have_count(0)
            expect(page.get_by_role("combobox", name="切换演示角色")).to_have_count(0)
            if role == "operator":
                page.get_by_role("button", name="连接器设计器", exact=True).click()
                expect(page.get_by_role("heading", name="连接器设计器", exact=True)).to_be_visible()
                page.screenshot(path=str(output / "v1-private-connectors.png"), full_page=True)
                page.get_by_role("button", name="本体映射工作台", exact=True).click()
                page.get_by_label("选择本体").select_option(receipt["ontology_id"])
                expect(page.locator(".react-flow__node")).to_have_count(7)
                expect(page.locator(".react-flow__node").first).to_be_visible()
                page.screenshot(path=str(output / "v1-private-ontology.png"), full_page=True)
                page.get_by_role("button", name="业务能力工厂", exact=True).click()
            page.screenshot(path=str(output / f"v1-private-{role}-factory.png"), full_page=True)
            page.reload()
            expect(page.get_by_role("heading", name="业务能力工厂", exact=True)).to_be_visible()
            assert not failures, failures
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_role("heading", name="业务能力工厂", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            context.close()
        browser.close()
    print(
        "PASS: private operator/viewer login, three workspaces, seven visible graph nodes, session reload, mobile width, zero page errors"
    )


if __name__ == "__main__":
    main()
