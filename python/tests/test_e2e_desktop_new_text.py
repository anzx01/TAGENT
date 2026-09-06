"""Real desktop E2E: record and replay creating a named text document."""
from __future__ import annotations

import ctypes
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uiautomation as ua
import win32com.client
from player import _show_desktop
from win32com.shell import shell, shellcon


BASE = "http://127.0.0.1:18899"
USER32 = ctypes.windll.user32


def api(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE + path, method=method, data=body,
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(request, timeout=15).read())


def mouse(x: int, y: int, right: bool = False) -> None:
    USER32.SetCursorPos(x, y)
    time.sleep(0.08)
    down, up = ((0x0008, 0x0010) if right else (0x0002, 0x0004))
    USER32.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.12)
    USER32.mouse_event(up, 0, 0, 0, 0)
    time.sleep(0.35)


def key_combo(*keys: int) -> None:
    for key in keys:
        USER32.keybd_event(key, USER32.MapVirtualKeyW(key, 0), 0, 0)
        time.sleep(0.04)
    for key in reversed(keys):
        USER32.keybd_event(key, 0, 2, 0)
        time.sleep(0.04)


def type_text(value: str) -> None:
    for char in value:
        code = USER32.VkKeyScanW(ord(char))
        vk, shift = code & 0xFF, (code >> 8) & 1
        if shift:
            USER32.keybd_event(0x10, 0, 0, 0)
        USER32.keybd_event(vk, USER32.MapVirtualKeyW(vk, 0), 0, 0)
        USER32.keybd_event(vk, 0, 2, 0)
        if shift:
            USER32.keybd_event(0x10, 0, 2, 0)
        time.sleep(0.04)


def find_menu_item(labels: tuple[str, ...], timeout: float = 5.0):
    expected = tuple(label.casefold() for label in labels)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # UIA exposes the Windows 11 flyout as a PopupWindowSiteBridge whose
        # useful MenuItems can be several levels below the desktop root. Do
        # not filter roots by class: the bridge's children are the authority.
        queue = list(ua.GetRootControl().GetChildren())
        visited = 0
        while queue and visited < 2500:
            control = queue.pop(0)
            visited += 1
            try:
                name = str(control.Name or "").strip()
                if (control.ControlTypeName == "MenuItemControl"
                        and any(label in name.casefold() for label in expected)):
                    rect = control.BoundingRectangle
                    if rect.right > rect.left and rect.bottom > rect.top:
                        return control, int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2)
                queue.extend(control.GetChildren())
            except Exception:
                continue
        time.sleep(0.2)
    return None


def wait_terminal(recording_id: str, timeout: float = 75.0) -> dict:
    deadline = time.monotonic() + timeout
    started = False
    state = {}
    while time.monotonic() < deadline:
        state = api("/replay/status")
        if state.get("recording_id") == recording_id and state.get("playing"):
            started = True
        if started and not state.get("playing"):
            return state
        time.sleep(0.3)
    raise AssertionError(f"replay did not finish: {state}")


def main() -> int:
    desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOP, None, 0))
    stem = f"TAGENT_E2E_{int(time.time())}"
    target = desktop / f"{stem}.txt"
    if target.exists():
        raise AssertionError(f"test target unexpectedly exists: {target}")

    try:
        # Expose the real shell before recording.  Win+D can be swallowed by
        # a foreground browser/assistant; Shell.Application is deterministic.
        _show_desktop()
        time.sleep(1.0)
        started = api("/record/start", "POST", {})
        recording_id = started["recording_id"]

        width, height = USER32.GetSystemMetrics(0), USER32.GetSystemMetrics(1)
        origin = (int(width * 0.70), int(height * 0.35))
        mouse(*origin, right=True)

        new_item = find_menu_item(("新建", "New"))
        if not new_item:
            raise AssertionError("live shell menu did not expose New")
        mouse(new_item[1], new_item[2])

        text_item = find_menu_item(("文本文档", "文本文件", "Text Document"))
        if not text_item:
            raise AssertionError("New submenu did not expose Text Document")
        mouse(text_item[1], text_item[2])
        time.sleep(0.8)
        type_text(stem)
        key_combo(0x0D)
        time.sleep(1.0)

        stopped = api("/record/stop", "POST", {})
        if not target.exists():
            raise AssertionError(f"recording demonstration did not create {target}")

        task = json.loads(Path(stopped["task_path"]).read_text(encoding="utf-8"))
        semantics = [step.get("semantic") for step in task.get("steps", [])]
        actions = [(step.get("action"), step.get("text", step.get("key", "")))
                   for step in task.get("steps", [])]
        if "shell_open_new_menu" not in semantics:
            raise AssertionError(f"recording missed New menu semantics: {semantics}")
        if "shell_create_text_document" not in semantics:
            raise AssertionError(f"recording missed Text Document semantics: {semantics}")
        if not any(action == "input_text" and stem in value for action, value in actions):
            raise AssertionError(f"recording missed filename input: {actions}")

        target.unlink()
        for attempt in (1, 2):
            api("/replay/start", "POST", {"recording_id": recording_id})
            final = wait_terminal(recording_id)
            if final.get("state") != "finished":
                raise AssertionError(f"replay {attempt} failed: {final}")
            if not target.exists():
                raise AssertionError(f"replay {attempt} produced no file: {target}")
            target.unlink()

        print(f"DESKTOP NEW TEXT E2E PASS: {recording_id}")
        return 0
    finally:
        # Only remove the unique artifact created by this test.
        target.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
