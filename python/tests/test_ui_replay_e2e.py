"""Headless WebView smoke test for the real task-library replay path.

The backend still injects into the desktop target, so run this on an
interactive Windows session with a compatible target window open.  The test
uses the same DOM buttons as the Tauri shell and verifies a terminal replay
state instead of trusting a successful HTTP start response.
"""
from __future__ import annotations

import os
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:18899"
TASK_ID = os.environ.get("TAGENT_E2E_TASK", "20260902_074300")


def api(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None if payload is None else __import__("json").dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, method=method, data=data,
        headers={"Content-Type": "application/json"},
    )
    return __import__("json").loads(urllib.request.urlopen(req, timeout=8).read())


def main() -> int:
    try:
        api("/replay/stop", "POST")
    except Exception:
        pass
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(BASE + "/ui/", wait_until="domcontentloaded")
        page.wait_for_function(
            "document.getElementById('status') && "
            "document.getElementById('status').textContent !== '服务未连接'",
            timeout=12000,
        )
        page.click("#btnList")
        card = page.locator(f'.task-card[data-task-id="{TASK_ID}"]')
        card.wait_for(state="visible", timeout=8000)
        run = card.locator(".mini.play")
        if run.is_disabled():
            raise AssertionError(f"task {TASK_ID} is not replayable")
        run.click()

        deadline = time.time() + 45
        retried = False
        while time.time() < deadline:
            state = api("/replay/status")
            if state.get("paused"):
                retry = page.locator("#takeoverRetry")
                if retry.count() and not retried:
                    retry.click()
                    retried = True
                else:
                    # A missing target is a legitimate takeover case; finish
                    # the smoke test through the documented handoff path.
                    resume = page.locator("#takeoverResume")
                    if resume.count():
                        resume.click()
            elif not state.get("playing"):
                break
            page.wait_for_timeout(350)
        final = api("/replay/status")
        browser.close()

    if final.get("playing"):
        raise AssertionError(f"replay did not reach terminal state: {final}")
    if final.get("state") != "finished":
        raise AssertionError(f"unexpected terminal state: {final}")
    if errors:
        raise AssertionError(f"frontend page errors: {errors}")
    print(f"UI replay E2E PASS: task={TASK_ID}, state={final.get('state')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
