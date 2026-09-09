"""调试5：自适应找'显示桌面'点击点 -> 右键桌面中心 -> OCR 找'新建'"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32
SW, SH = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)

import win32gui
from services.ocr_locator import capture_and_find


def foreground():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


def click(x, y, delay=0.5):
    u32.SetCursorPos(x, y)
    time.sleep(0.15)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(delay)


def show_desktop():
    for x in (SW - 4, SW - 10, SW - 25, SW - 60):
        for y in (SH - 40, SH - 20):
            click(x, y, 0.8)
            if "Program Manager" in foreground():
                return (x, y)
    return None


p = show_desktop()
print("显示桌面点击点:", p, "| 前台:", repr(foreground()))
assert p, "未找到显示桌面按钮"

cx, cy = SW // 2, SH // 2
u32.SetCursorPos(cx, cy)
time.sleep(0.3)
u32.mouse_event(0x0008, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0010, 0, 0, 0, 0)
time.sleep(1.2)
pos = capture_and_find("新建")
print("OCR 找'新建':", pos)

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
