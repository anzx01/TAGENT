"""查找记事本窗口标题"""
import subprocess, time, win32gui

subprocess.Popen(["notepad.exe"])
time.sleep(2)

def cb(h, r):
    r.append((h, win32gui.GetWindowText(h)))
    return True

r = []
win32gui.EnumWindows(cb, r)
for h, t in r:
    if t and ("notepad" in t.lower() or "记事本" in t or "无标题" in t or "untitled" in t.lower()):
        print(f"hwnd={h} title='{t}'")

# 也查看所有有标题的窗口
print("\n--- 所有有标题窗口 ---")
for h, t in r:
    if t:
        print(f"  hwnd={h} title='{t}'")
