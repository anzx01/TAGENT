"""调试：遍历 WebView2 UIA 树，读状态栏文本"""
import time
import uiautomation as auto


def walk(control, depth=0, limit=40):
    if depth > 6 or limit <= 0:
        return limit
    name = (control.Name or "")[:60]
    r = control.BoundingRectangle
    print("  " * depth + f"{control.ControlTypeName}: {name!r} {r}")
    limit -= 1
    for c in control.GetChildren():
        limit = walk(c, depth + 1, limit)
    return limit


win = auto.WindowControl(searchDepth=1, Name="智能操作助手")
btn_list = win.ButtonControl(Name="任务库")
btn_list.Click()
time.sleep(3)
walk(win)