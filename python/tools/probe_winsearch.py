"""调试24：Win 键 -> 输入 notepad -> 回车 -> 记事本启动？"""
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


def combo(*vks, hold=0.06, tail=0.4):
    for vk in vks:
        key(vk, 0x0001 if vk == 0x5B else 0)
        time.sleep(hold)
    for vk in reversed(vks):
        key(vk, (0x0001 | KEYEVENTF_KEYUP) if vk == 0x5B else KEYEVENTF_KEYUP)
        time.sleep(hold)
    time.sleep(tail)


import win32gui


def fg():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


key(0x5B, KEYEVENTF_EXTENDEDKEY)
key(0x5B, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
time.sleep(1.5)
print("Win 后:", repr(fg()))

for ch in "notepad":
    vk = ord(ch.upper())
    key(vk)
    key(vk, KEYEVENTF_KEYUP)
    time.sleep(0.12)
time.sleep(2.0)                       # 等搜索结果
print("输入后:", repr(fg()))

combo(0x0D, tail=4.0)                 # 回车启动第一个结果
print("回车后:", repr(fg()))
