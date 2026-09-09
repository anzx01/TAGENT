"""调试28：notepad 直接打开桌面新文件路径 -> 输入 -> Ctrl+S 静默保存"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32

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


def combo(*vks, tail=0.4):
    for vk in vks:
        key(vk)
        time.sleep(0.06)
    for vk in reversed(vks):
        key(vk, KEYEVENTF_KEYUP)
        time.sleep(0.06)
    time.sleep(tail)


def type_digits(text):
    for ch in text:
        vk = ord(ch.upper())
        key(vk)
        key(vk, KEYEVENTF_KEYUP)
        time.sleep(0.1)


import win32gui

TARGET = Path(r"C:\Users\Administrator\Desktop\123.txt")
TARGET.unlink(missing_ok=True)

subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
time.sleep(1.0)
subprocess.Popen(["notepad", str(TARGET)])
time.sleep(2.5)
print("前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

type_digits("123")
time.sleep(0.5)
combo(0x11, 0x53, tail=1.5)                     # Ctrl+S 静默保存
print("保存后:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
print("文件存在:", TARGET.exists(), "| 内容:",
      repr(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else None)
combo(0x12, 0x73)
time.sleep(0.8)
print("关闭后:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
