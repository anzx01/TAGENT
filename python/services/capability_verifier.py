"""Explicit, local postconditions for composed capabilities."""


def verify_postcondition(condition: dict, *, window_title: str = "",
                         text_found: bool = False) -> tuple[bool, str]:
    # any_of:任一子条件满足即通过(正向成功+反向拒绝成对回归,移植 createNamedRecord 双期望)
    if isinstance(condition.get("any_of"), list):
        results = [verify_postcondition(c, window_title=window_title, text_found=text_found)
                   for c in condition["any_of"] if isinstance(c, dict)]
        if not results:
            return False, "any_of 为空"
        passed = any(ok for ok, _ in results)
        return passed, "任一完成条件满足" if passed else "；".join(d for _, d in results)
    kind = condition.get("type")
    expected = str(condition.get("value", "")).strip()
    if not expected:
        return False, "完成条件未填写"
    if kind == "window":
        passed = expected.casefold() in window_title.casefold()
        if not passed:
            # 标题漂移容忍（与 player/replay_gate 同源）：录制带状态后缀、
            # 验证时后缀消失不应让完成条件永远不通过
            from services.title_match import titles_similar
            passed = titles_similar(expected, window_title)
        return passed, "窗口标题匹配" if passed else f"当前窗口不包含「{expected}」"
    if kind == "text":
        return text_found, "屏幕文字已找到" if text_found else f"屏幕未找到「{expected}」"
    return False, "不支持的完成条件类型"


def verify_current_screen(condition: dict) -> tuple[bool, str]:
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) if hwnd else ""

        def one(cond: dict) -> tuple[bool, str]:
            text_found = False
            if cond.get("type") == "text" and hwnd:
                from services.ocr_locator import capture_and_find
                text_found = capture_and_find(str(cond.get("value", "")), hwnd=hwnd) is not None
            return verify_postcondition(cond, window_title=title, text_found=text_found)

        if isinstance(condition.get("any_of"), list):
            results = [one(c) for c in condition["any_of"] if isinstance(c, dict)]
            if not results:
                return False, "any_of 为空"
            passed = any(ok for ok, _ in results)
            return passed, "任一完成条件满足" if passed else "；".join(d for _, d in results)
        return one(condition)
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
