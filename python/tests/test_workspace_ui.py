"""Headless smoke test for the Caddi/Skill Recorder-inspired workspace shell."""
from __future__ import annotations

from playwright.sync_api import sync_playwright


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto("http://127.0.0.1:18899/ui/", wait_until="domcontentloaded")
        page.click("#btnList")
        tabs = page.locator(".workspace-tab")
        assert tabs.count() == 4
        page.locator('[data-tab="tasks"]').click()
        cards = page.locator(".task-card").filter(has=page.locator(".mini.play"))
        cards.first.wait_for(state="visible", timeout=10000)
        opened = False
        for i in range(min(cards.count(), 20)):
            cards.nth(i).locator(".mini.plan").click()
            page.wait_for_timeout(150)
            if page.locator("#modalMask.show").count():
                opened = True
                break
        assert opened, "no task produced a plan modal"
        assert "Automation Plan" in page.locator("#modalBox").inner_text()
        page.locator("#modalMask").locator("button").last.click()
        page.locator('[data-tab="history"]').click()
        page.locator("#historyList").wait_for(state="visible", timeout=5000)
        assert not errors, errors
        browser.close()
    print("workspace_ui PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
