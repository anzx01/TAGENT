"""回放前置门禁(移植 desktop-recorder-skill 的状态指纹/dry-run 思想)。

只做廉价的窗口/标签存在性探针——不截屏、不 OCR,失败时拒绝开跑,
让"起始状态不对"在回放启动前就暴露,而不是跑到第 3 步才定位失败。
"""
from __future__ import annotations

from services.title_match import titles_similar


def window_exists(title: str, enumerate_windows=None) -> bool:
    """窗口存在性探针:包含匹配(去掉未保存 *) + 标题漂移容忍
    (录制带 [管理员模式]/扩展名等状态后缀、回放时后缀消失)。"""
    if not title:
        return True
    title = str(title).lstrip("*").strip()
    if not title:
        return True
    if enumerate_windows is None:
        def enumerate_windows():
            import win32gui
            out = []
            win32gui.EnumWindows(lambda h, l: (l.append(win32gui.GetWindowText(h)), True)[1], out)
            return out
    needle = title.casefold()
    titles = [str(t) for t in enumerate_windows() if str(t or "").strip()]
    return any(needle in t.casefold() for t in titles) or any(
        titles_similar(title, t) for t in titles)


def label_visible(window_title: str, label: str, finder=None) -> bool:
    """UIA 名称级廉价探针:目标窗口下是否存在指定名称的控件。"""
    if not label:
        return True
    if finder is None:
        def finder(win, text):
            import uiautomation as ua
            root = ua.ControlFromHandle(_find_hwnd(win)) if win else ua.GetRootControl()
            if root is None:
                return False
            return bool(root.Control(searchDepth=12, Name=text).Exists(0.3))
    try:
        return bool(finder(window_title, label))
    except Exception:
        return False


def _find_hwnd(title: str) -> int:
    import win32gui
    needle = title.casefold()
    found = []
    win32gui.EnumWindows(
        lambda h, l: (str(win32gui.GetWindowText(h)).casefold().find(needle) >= 0
                      and l.append(h), True)[1], found)
    return found[0] if found else 0


def check_preflight(task: dict, *, probe_window=window_exists) -> tuple[bool, list[str]]:
    """执行 task['preflight'] 声明;未声明时按首个人机交互步骤的目标窗口推导一条。

    返回 (通过?, 失败原因列表)。preflight 项:{kind: window, value: 标题子串}。
    """
    items = [p for p in (task.get("preflight") or []) if isinstance(p, dict)]
    if not items:
        for step in task.get("steps", []):
            win = str(step.get("window", "") or "")
            if win and step.get("action") in ("input_text", "click", "key_press",
                                              "double_click", "right_click"):
                items = [{"kind": "window", "value": win}]
                break
    failures: list[str] = []
    for item in items:
        if item.get("kind") == "window":
            value = str(item.get("value", "") or "").lstrip("*").strip()
            if value and not probe_window(value):
                failures.append(f"目标窗口未打开: {value}")
        # shortcut: 未知的 preflight 项按通过处理,避免旧数据被新字段卡死;需要更多探针类型时再扩展
    return (not failures), failures
