"""调试17：Shift+F10 -> N(新建) -> T(文本文档) 纯键盘导航"""
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

DESKTOP = Path(r"C:\Users\Administrator\Desktop")


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


# 清理旧产物
for p in DESKTOP.glob("新建*.txt"):
    p.unlink()
    print("清理:", p.name)

assert win_d()
cx, cy = SW // 2, SH // 2
u32.SetCursorPos(cx, cy)
time.sleep(0.2)
u32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.04)
u32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(0.5)

key(0x10); key(0x79); key(0x79, KEYEVENTF_KEYUP); key(0x10, KEYEVENTF_KEYUP)
time.sleep(1.0)
key(0x4E)                                   # N -> 新建
time.sleep(1.0)
key(0x54)                                   # T -> 文本文档
time.sleep(1.5)

created = list(DESKTOP.glob("新建*.txt"))
print("重命名态产物:", [p.name for p in created])
print("前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
if created:
    print("OK: 键盘导航成功，进入重命名态")
