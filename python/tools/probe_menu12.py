"""调试12：右键前后 EnumWindows 差集 -> 对菜单窗口做 UIA 树 dump"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32
SW, SH = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT), ("pad", ctypes.c_ubyte * 8)]


def key(vk, flags=0):
    ki = KEYBDINPUT(vk, 0, flags, 0, None)
    u32.SendInput(1, ctypes.byref(INPUT(INPUT_KEYBOARD, ki)), ctypes.sizeof(INPUT))


import win32gui
import uiautomation as ua


def win_d():
    for _ in range(2):
        key(0x5B, KEYEVENTF_EXTENDEDKEY)
        key(0x44)
        key(0x44, KEYEVENTF_KEYUP)
        key(0x5B, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
        time.sleep(1.2)
        if "Program Manager" in win32gui.GetWindowText(win32gui.GetForegroundWindow()):
            return True
    return False


def visible_windows():
    out = {}

    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            out[h] = (win32gui.GetWindowText(h), win32gui.GetClassName(h))
        return True

    win32gui.EnumWindows(cb, None)
    return out


assert win_d()
before = visible_windows()
cx, cy = SW // 2, SH // 2
u32.SetCursorPos(cx, cy)
time.sleep(0.3)
u32.mouse_event(0x0008, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0010, 0, 0, 0, 0)
time.sleep(1.5)

after = visible_windows()
new = {h: v for h, v in after.items() if h not in before}
print("新增窗口:")
for h, (title, cls) in new.items():
    print(f"  hwnd={h} class={cls!r} title={title!r}")

# 对每个新窗口 dump UIA 树（两层）
for h in new:
    try:
        ctrl = ua.ControlFromHandle(h)
        print(f"--- UIA {h} ---")
        child = ctrl.GetFirstChildControl()
        depth1 = 0
        while child and depth1 < 15:
            r = child.BoundingRectangle
            if (child.Name or "").strip():
                print(f"  L1 {child.ControlTypeName} {child.Name!r} ({r.left},{r.top},{r.right},{r.bottom})")
            gc = child.GetFirstChildControl()
            depth2 = 0
            while gc and depth2 < 8:
                r2 = gc.BoundingRectangle
                if (gc.Name or "").strip():
                    print(f"      L2 {gc.ControlTypeName} {gc.Name!r} ({r2.left},{r2.top},{r2.right},{r2.bottom})")
                gc = gc.GetNextSiblingControl()
                depth2 += 1
            child = child.GetNextSiblingControl()
            depth1 += 1
    except Exception as e:
        print("  UIA 失败:", e)

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
