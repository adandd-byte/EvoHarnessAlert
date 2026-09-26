"""Isolated browser acceptance checks; all backend API traffic is intercepted."""
import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright, expect

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8091"
OUTPUT = Path(__file__).resolve().parents[1] / "target/ui"
PAYLOAD = '<img src=x onerror="window.injected=true"> 测试文本'


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    checks, errors, calls = [], [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.getenv("CHROMIUM_EXECUTABLE") or None)
        page = await browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda e: errors.append(str(e)))
        fixture = {"status": 200, "empty": False, "write_fail": False, "delay": False, "offline": False}
        alerts = [{"id": i, "title": PAYLOAD if i == 1 else f"后端告警 {i}", "severity": "P1", "alertType": "problem", "description": "真实接口返回描述", "labels": {"service": f"service-{i}", "env": "production"}, "status": "firing"} for i in [1, 2, 3]]
        incidents = [{"id": i, "lastAlertId": i, "status": "OPEN", "severity": "P1", "summary": "接口研判摘要", "owner": "", "rootCauseHint": PAYLOAD} for i in [1, 2]]

        async def route_api(route):
            path = route.request.url.split("/api/")[-1]
            method = route.request.method
            calls.append((method, path))
            if fixture["offline"]:
                await route.abort("failed")
                return
            status = fixture["status"]
            if method == "POST" and fixture["write_fail"]:
                status = 500
            result = {}
            if path == "profile":
                result = {"username": "admin", "roles": [{"authority": "ROLE_ADMIN"}]}
            elif path == "admin/alerts":
                result = [] if fixture["empty"] else alerts
            elif path == "admin/incidents":
                result = [] if fixture["empty"] else incidents
            elif path == "admin/agent-traces":
                result = [{"alertId": 1, "incidentId": 1, "agentSteps": [{"agent": "Safety", "action": PAYLOAD}]}]
            elif path.endswith("/notes"):
                incident_id = int(path.split("/")[2])
                if fixture["delay"] and incident_id == 1:
                    await asyncio.sleep(0.7)
                result = [{"actor": "后端值班员", "note": f"记录归属事件 {incident_id}", "createdAt": "2026-09-25T10:00:00Z"}]
                if method == "POST":
                    result = json.loads(route.request.post_data)
            elif path.endswith(("/ack", "/resolve")):
                incident_id = int(path.split("/")[2])
                result = next(x for x in incidents if x["id"] == incident_id)
                if status == 200:
                    result["status"] = "RESOLVED" if path.endswith("/resolve") else "ACKNOWLEDGED"
            elif path == "alerts/webhook":
                result = {"alertId": 4, "message": "ok"}
            await route.fulfill(status=status, content_type="application/json", body=json.dumps(result))

        await page.route("**/api/**", route_api)
        await page.goto(URL)
        await expect(page.locator(".alert-row")).to_have_count(10)
        await expect(page.locator("#resultCount")).to_have_text("20 条")
        await page.select_option("#priority", "P0")
        await page.select_option("#type", "HOST")
        await expect(page.locator(".alert-row")).to_have_count(1)
        await page.fill("#search", "不存在")
        await expect(page.locator("#table")).to_contain_text("没有符合筛选")
        await page.click("#reset")
        await page.click('[data-page="1"]')
        await expect(page.locator("#pagination")).to_contain_text("11–20 / 20")
        await page.click('[data-page="-1"]')
        await page.click('[data-sort="severity"]')
        await expect(page.locator(".alert-row").first).to_contain_text("P3")
        await page.click('[data-sort="severity"]')
        await page.locator('[data-id="ALERT-01-normal"]').click()
        await page.select_option("#variant", "ALERT-01-timeout")
        await page.click('[data-tab="evidence"]')
        await expect(page.locator("#detail")).to_contain_text("查询超时")
        await page.locator(".evidence summary").first.click()
        await expect(page.locator(".evidence[open]")).to_have_count(1)
        await page.select_option("#variant", "ALERT-01-conflict")
        await expect(page.locator("#detail")).to_contain_text("不同实例组")
        await page.click('[data-tab="notes"]')
        await page.fill("#noteText", PAYLOAD)
        await page.locator("#noteForm button").click()
        await expect(page.locator(".note")).to_contain_text(PAYLOAD)
        await page.click('[data-action="ack"]')
        await expect(page.locator(".detail-meta")).to_contain_text("处理中")
        await page.click('[data-action="resolve"]')
        await page.fill("#resolveNote", "已核验恢复")
        await page.locator('#resolveForm [type="submit"]').click()
        await expect(page.locator(".detail-meta")).to_contain_text("已关闭")
        await page.select_option("#status", "RESOLVED")
        await expect(page.locator(".alert-row")).to_have_count(1)
        await page.click("#reset")
        await page.click('[data-view="dataset"]')
        await expect(page.locator("#resultCount")).to_have_text("100 条")
        await page.select_option("#variantFilter", "tenant_denied")
        await expect(page.locator("#resultCount")).to_have_text("20 条 / 共 100 条")
        await page.locator("[data-id]").first.click()
        await page.click('[data-action="load"]')
        await expect(page.locator("#pageTitle")).to_have_text("告警工作台")
        async with page.expect_download() as dl:
            await page.click("#download")
        assert (await dl.value).suggested_filename == "alert-replay-v1.json"
        await page.click("#newAlert")
        await page.fill("#rawInput", PAYLOAD)
        await page.click("#submitAlert")
        await expect(page.locator(".detail-fixed h2")).to_have_text(PAYLOAD)
        assert await page.locator("#detail img").count() == 0
        assert await page.evaluate("window.injected === undefined")
        assert calls == [], "Simulation must never call backend APIs"
        checks.extend(["combined_filters", "sorting", "pagination", "variants", "evidence", "notes", "ack", "resolve_confirmation", "dataset", "export", "simulated_ingest", "xss", "simulation_isolation"])

        # Capture the principal workbench, then verify the mobile list/detail transition.
        await page.reload()
        await expect(page.locator(".alert-row")).to_have_count(10)
        for width in [1440, 1920, 768, 390]:
            await page.set_viewport_size({"width": width, "height": 1050 if width > 1000 else 844})
            await page.screenshot(path=str(OUTPUT / f"width-{width}.png"), full_page=True)
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width}"
            assert await page.evaluate("document.querySelector('.pagination').getBoundingClientRect().bottom <= document.querySelector('footer').getBoundingClientRect().top"), f"footer overlap at {width}"
        await page.locator("[data-id]").first.click()
        await expect(page.locator(".detail")).to_be_visible()
        await page.click('[data-tab="evidence"]')
        await page.screenshot(path=str(OUTPUT / "mobile-detail.png"), full_page=True)
        await page.click('[data-action="back"]')
        await expect(page.locator(".table-pane")).to_be_visible()
        checks.append("responsive_4_viewports")
        await page.set_viewport_size({"width": 1440, "height": 1050})
        await page.click('[data-view="dataset"]')
        await page.screenshot(path=str(OUTPUT / "dataset-desktop.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path=str(OUTPUT / "dataset-mobile.png"), full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.set_viewport_size({"width": 1440, "height": 1050})
        await page.click('[data-view="alerts"]')
        await page.click('[data-action="expand"]')
        await expect(page.locator(".table-pane")).not_to_be_visible()
        await page.click('[data-action="expand"]')
        await expect(page.locator(".table-pane")).to_be_visible()
        await page.click("#collapse")
        await expect(page.locator("body")).to_have_class("collapsed")
        await page.click("#collapse")
        checks.extend(["detail_expand", "sidebar_collapse", "no_footer_overlap"])

        for status in [401, 403, 500]:
            fixture["status"] = status
            await page.click("#connect")
            await page.fill("#username", "admin")
            await page.fill("#password", "test")
            await page.locator('#loginForm [type="submit"]').click()
            await expect(page.locator("#loginError")).to_contain_text(str(status))
            await page.keyboard.press("Escape")
        fixture["status"] = 200
        await page.click("#connect")
        await page.fill("#password", "test")
        await page.locator('#loginForm [type="submit"]').click()
        await expect(page.locator("#connection")).to_have_text("断开后端")
        await expect(page.locator(".alert-row")).to_have_count(3)
        await page.click('[data-view="dataset"]')
        await page.click('[data-view="alerts"]')
        await expect(page.locator(".alert-row")).to_have_count(3)
        await expect(page.locator("#environment")).to_have_text("后端接入")
        await page.click('[data-id="3"]')
        await expect(page.locator('[data-action="ack"]')).to_be_disabled()
        await expect(page.locator(".action-reason")).to_contain_text("未关联事件")
        await page.click('[data-id="1"]')
        await page.click('[data-tab="flow"]')
        assert await page.locator("#detail img").count() == 0
        fixture["delay"] = True
        await page.click('[data-tab="notes"]')
        await page.click('[data-id="2"]')
        await page.click('[data-tab="notes"]')
        await expect(page.locator(".note")).to_contain_text("记录归属事件 2")
        await page.wait_for_timeout(850)
        await expect(page.locator(".note")).not_to_contain_text("记录归属事件 1")
        fixture["delay"] = False
        await page.fill("#noteText", PAYLOAD)
        await page.locator("#noteForm button").click()
        await expect(page.locator("#detail")).to_contain_text(PAYLOAD)
        fixture["write_fail"] = True
        await page.click('[data-action="ack"]')
        await expect(page.locator("#notice")).to_contain_text("500")
        await expect(page.locator(".detail-meta")).to_contain_text("待接手")
        await page.click("#newAlert")
        await page.fill("#rawInput", "后端投递失败")
        await page.click("#submitAlert")
        await expect(page.locator("#submitError")).to_contain_text("500")
        await expect(page.locator("#alertDialog")).to_be_visible()
        fixture["write_fail"] = False
        await page.click("#submitAlert")
        await expect(page.locator("#alertDialog")).not_to_be_visible()
        await page.click('[data-action="ack"]')
        await expect(page.locator(".detail-meta")).to_contain_text("处理中")
        await page.click('[data-action="resolve"]')
        await page.fill("#resolveNote", "后端恢复验证完成")
        fixture["write_fail"] = True
        await page.locator('#resolveForm [type="submit"]').click()
        await expect(page.locator("#resolveError")).to_contain_text("500")
        fixture["write_fail"] = False
        await page.locator('#resolveForm [type="submit"]').click()
        await expect(page.locator(".detail-meta")).to_contain_text("已关闭")
        fixture["status"] = 500
        await page.click("#refresh")
        await expect(page.locator("#table")).to_contain_text("数据加载失败")
        fixture["status"] = 200
        fixture["empty"] = True
        await page.click("[data-retry]")
        await expect(page.locator("#table")).to_contain_text("暂无告警")
        fixture["empty"] = False
        fixture["offline"] = True
        await page.click("#refresh")
        await expect(page.locator("#table")).to_contain_text("后端连接中断")
        fixture["offline"] = False
        await page.click("[data-retry]")
        await expect(page.locator(".alert-row")).to_have_count(3)
        await page.click("#connect")
        await expect(page.locator("#environment")).to_have_text("演练数据")
        assert await page.evaluate("window.injected === undefined")
        assert await page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
        dataset = json.loads((Path(__file__).resolve().parents[1] / "app/static/fixtures/alert-replay-v1.json").read_text())
        dataset["cases"][0]["toolSnapshots"][0]["result"]["summary"] = PAYLOAD
        await page.route("**/fixtures/alert-replay-v1.json", lambda route: route.fulfill(content_type="application/json", body=json.dumps(dataset)))
        await page.reload()
        await expect(page.locator(".alert-row")).to_have_count(10)
        await page.click('[data-tab="evidence"]')
        await expect(page.locator(".evidence").first).to_contain_text(PAYLOAD)
        assert await page.locator("#detail img").count() == 0
        assert await page.evaluate("window.injected === undefined")
        checks.append("evidence_xss")
        assert not errors, errors
        checks.extend(["401_403_500", "live_connection", "credentials_survive_navigation", "missing_incident", "stale_notes", "live_notes", "write_failure", "live_ingest", "live_ack_resolve", "empty_backend", "disconnect_retry", "memory_only_auth"])
        await browser.close()
    result = {"passed": True, "total_checks": len(checks), "checks": checks, "screenshots": str(OUTPUT), "backend": "intercepted API; not a real MySQL integration"}
    (OUTPUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
