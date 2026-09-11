"""Handle WPS Writer's local Save As dialog by accessible control names."""
from pathlib import Path


def save_writer_dialog(path):
    import uiautomation as ua
    with ua.UIAutomationInitializerInThread():
        return _save_writer_dialog(path)


def _save_writer_dialog(path):
    import psutil
    import uiautomation as ua
    import win32gui
    import win32process
    target = Path(path).resolve()
    if target.exists():
        return False
    hwnd = win32gui.GetForegroundWindow()
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if psutil.Process(pid).name().lower() != "wps.exe":
        return False
    root = ua.ControlFromHandle(hwnd)
    if root.Name not in ("\u53e6\u5b58\u4e3a", "Save As"):
        return False
    field = root.ComboBoxControl(Name="\u6587\u4ef6\u540d\u79f0(N)", searchDepth=8)
    save = root.ButtonControl(Name="\u4fdd\u5b58(S)", searchDepth=8)
    if not field.Exists(0.2) or not save.Exists(0.2):
        return False
    edit = field.EditControl(searchDepth=2)
    if not edit.Exists(0.2):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    edit.GetValuePattern().SetValue(str(target))
    if edit.GetValuePattern().Value != str(target):
        raise RuntimeError("WPS filename value did not match")
    save.GetInvokePattern().Invoke()
    return True
