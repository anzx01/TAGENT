"""调试7：SendInput(带 KEYEVENTF_EXTENDEDKEY) 注入 Win+D -> 验证前台变桌面"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32

import win32gui

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
    inp = INPUT(INPUT_KEYBOARD, ki)
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def win_d():
    key(0x5B, KEYEVENTF_EXTENDEDKEY)                       # LWIN down
    key(0x44)                                             # D down
    key(0x44, KEYEVENTF_KEYUP)                            # D up
    key(0x5B, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)    # LWIN up


print("前台(前):", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
win_d()
time.sleep(1.2)
print("前台(后):", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
