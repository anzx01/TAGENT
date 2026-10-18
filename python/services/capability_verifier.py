"""Explicit, local postconditions for composed capabilities."""


def verify_postcondition(condition: dict, *, window_title: str = "",
                         text_found: bool = False) -> tuple[bool, str]:
    kind = condition.get("type")
    expected = str(condition.get("value", "")).strip()
    if not expected:
        return False, "完成条件未填写"
    if kind == "window":
        passed = expected.casefold() in window_title.casefold()
        return passed, "窗口标题匹配" if passed else f"当前窗口不包含「{expected}」"
    if kind == "text":
        return text_found, "屏幕文字已找到" if text_found else f"屏幕未找到「{expected}」"
    return False, "不支持的完成条件类型"


def verify_current_screen(condition: dict) -> tuple[bool, str]:
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) if hwnd else ""
        text_found = False
        if condition.get("type") == "text" and hwnd:
            from services.ocr_locator import capture_and_find
            text_found = capture_and_find(str(condition.get("value", "")), hwnd=hwnd) is not None
        return verify_postcondition(condition, window_title=title, text_found=text_found)
    except Exception:
        return False, "无法读取当前窗口或屏幕"


def evaluate_boundary(condition: dict, verify, on_failure) -> dict:
    passed, detail = verify(condition)
    if passed:
        return {"status": "ok", "verification": "passed", "detail": detail}
    action = on_failure(detail)
    if action == "skip":
        return {"status": "ok", "verification": "human_confirmed", "detail": detail}
    if action == "retry":
        passed, detail = verify(condition)
        return {"status": "ok" if passed else "failed",
                "verification": "passed" if passed else "failed", "detail": detail}
    return {"status": "failed", "verification": "failed", "detail": detail}
