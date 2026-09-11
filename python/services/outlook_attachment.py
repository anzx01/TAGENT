"""Attach a local file using New Outlook's accessible attachment menu."""
from pathlib import Path
import time


def visual_click(hwnd, target, double=False):
    import io
    import math
    import win32gui
    from PIL import ImageGrab
    from services.llm_heal import get_healer
    from services.visual_agent import _exec_click, _exec_double_click
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError("Outlook focus changed")
    rect = win32gui.GetWindowRect(hwnd)
    image = ImageGrab.grab().crop(rect)
    view = image.copy()
    view.thumbnail((1280, 1280))
    buffer = io.BytesIO()
    view.save(buffer, format="PNG")
    result = get_healer().plan_visual(
        f"Find {target}. Return action=click and x,y in this {view.width}x{view.height} screenshot. "
        "Return null if absent. Do not choose Send or any suggested document.", buffer.getvalue())
    if not isinstance(result, dict) or result.get("action") != "click":
        raise RuntimeError("Attachment target not identified")
    x, y = result.get("x"), result.get("y")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y)) or not (0 <= x < view.width and 0 <= y < view.height):
        raise RuntimeError("Invalid attachment coordinates")
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError("Outlook focus changed")
    execute = _exec_double_click if double else _exec_click
    ok, reason = execute({"x": rect[0]+x*image.width/view.width, "y": rect[1]+y*image.height/view.height}, 1)
    if not ok:
        raise RuntimeError(reason)


def attach_file(path):
    import psutil
    import uiautomation as ua
    import win32gui
    import win32process
    target = Path(path).resolve(strict=True)
    if not target.is_file():
        raise ValueError("Attachment must be a file")
    hwnd = win32gui.GetForegroundWindow()
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if psutil.Process(pid).name().lower() != "olk.exe":
        raise RuntimeError("Outlook must own the foreground window")
    root = ua.ControlFromHandle(hwnd)
    attach = root.ButtonControl(RegexName=r"^(\u9644\u52a0\u6587\u4ef6|\u9644\u52a0|Attach file|Attach)$", searchDepth=20)
    if attach.Exists(1):
        attach.Click()
    else:
        visual_click(hwnd, "the paperclip Attach file toolbar button")
    time.sleep(.5)
    browse = root.Control(RegexName=r"^(\u6d4f\u89c8\u6b64\u8ba1\u7b97\u673a|Browse this computer)$", searchDepth=20)
    if browse.Exists(1):
        browse.Click()
    else:
        visual_click(hwnd, "Browse this computer / \u6d4f\u89c8\u6b64\u8ba1\u7b97\u673a in the attachment menu")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        dialog = win32gui.GetForegroundWindow()
        _, owner = win32process.GetWindowThreadProcessId(dialog)
        if win32gui.GetClassName(dialog) == "#32770" and owner == pid:
            native = ua.ControlFromHandle(dialog)
            filename = native.EditControl(AutomationId="1148", searchDepth=5)
            if not filename.Exists(.2):
                raise RuntimeError("File picker filename field unavailable")
            filename.GetValuePattern().SetValue(str(target))
            if filename.GetValuePattern().Value != str(target):
                raise RuntimeError("Attachment path verification failed")
            native.ButtonControl(AutomationId="1", searchDepth=3).GetInvokePattern().Invoke()
            return target
        time.sleep(.2)
    raise RuntimeError("Outlook file picker did not appear")
