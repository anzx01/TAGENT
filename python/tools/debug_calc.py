"""调试计算器 UI 树"""
import subprocess, time, win32gui
import uiautomation as ua

subprocess.Popen(["calc.exe"])
time.sleep(3)
hwnd = win32gui.FindWindow(None, "计算器")
if not hwnd:
    hwnd = win32gui.FindWindow(None, "Calculator")
if not hwnd:
    r = []
    def cb(h, r):
        t = win32gui.GetWindowText(h)
        if "计算" in t or "calc" in t.lower():
            r.append(h)
        return True
    win32gui.EnumWindows(cb, r)
    if r: hwnd = r[0]
assert hwnd, "找不到计算器"
print(f"hwnd={hwnd} title='{win32gui.GetWindowText(hwnd)}'")

root = ua.ControlFromHandle(hwnd)
if not root:
    print("UIA 获取失败")
    exit()

def dump(ctrl, depth=0, max_depth=5):
    if depth > max_depth:
        return
    try:
        name = ctrl.Name[:50] if ctrl.Name else ""
        ctype = ctrl.ControlTypeName
        cls = ctrl.ClassName
        aid = ctrl.AutomationId
        print(f"{'  '*depth}[{ctype}] name='{name}' class='{cls}' auto_id='{aid}'")
        for child in ctrl.GetChildren():
            dump(child, depth+1, max_depth)
    except Exception as e:
        print(f"{'  '*depth}ERROR: {e}")

dump(root)
