"""调试27：另存为纯键盘导航全链验证
Ctrl+S -> Ctrl+L 输桌面路径回车 -> Alt+N 文件名 -> Ctrl+A -> 123.txt -> 回车
"""
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


def type_text(text):
    for ch in text:
        vk = u32.VkKeyScanW(ord(ch)) & 0xFF
        key(vk)
        key(vk, KEYEVENTF_KEYUP)
        time.sleep(0.07)


import win32gui

DESKTOP = Path(r"C:\Users\Administrator\Desktop")
TARGET = DESKTOP / "123.txt"
TARGET.unlink(missing_ok=True)

subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
time.sleep(1.0)
subprocess.Popen(["notepad"])
time.sleep(2.5)
print("前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

type_text("123")
time.sleep(0.5)
combo(0x11, 0x53, tail=2.0)                     # Ctrl+S -> 另存为
print("Ctrl+S 后:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

combo(0x11, 0x4C, tail=0.5)                     # Ctrl+L 地址栏
type_text(str(DESKTOP))
combo(0x0D, tail=1.0)                           # 回车 -> 桌面
print("路径后:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

combo(0x12, 0x4E, tail=0.4)                     # Alt+N 文件名框
combo(0x11, 0x41, tail=0.3)                     # Ctrl+A
type_text("123.txt")
time.sleep(0.4)
combo(0x0D, tail=2.0)                           # 回车保存
time.sleep(1.0)
print("保存后前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
print("文件存在:", TARGET.exists())
if TARGET.exists():
    print("内容:", repr(TARGET.read_text(encoding="utf-8")))
combo(0x12, 0x73)                               # Alt+F4
time.sleep(0.8)
