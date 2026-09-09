"""调试20：验证 Win+R 与 Win 键（开始菜单）两条打开途径（不录制）"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32

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


def fg():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


print("前台:", repr(fg()))
# --- 试验 1: Win+R ---
key(0x5B, KEYEVENTF_EXTENDEDKEY)
key(0x52)
key(0x52, KEYEVENTF_KEYUP)
key(0x5B, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
time.sleep(1.5)
print("Win+R 后前台:", repr(fg()))

# 找"运行"窗口
def find_window(sub):
    res = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and sub in win32gui.GetWindowText(h):
            res.append(win32gui.GetWindowText(h))
        return True
    win32gui.EnumWindows(cb, None)
    return res

print("运行窗口:", find_window("运行"))
# 关掉可能的运行框
u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
time.sleep(0.5)

# --- 试验 2: Win 键（开始菜单） ---
key(0x5B, KEYEVENTF_EXTENDEDKEY)
key(0x5B, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
time.sleep(1.5)
print("Win 后前台:", repr(fg()))
print("开始菜单窗口:", find_window("开始")[:3])
u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
