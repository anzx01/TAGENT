"""调试31：v4 最终链路验证（cwd=桌面 + 双剪贴板）"""
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


def set_clip(text):
    subprocess.run(["powershell", "-command", f"Set-Clipboard -Value '{text}'"], check=True)


import win32gui

DESKTOP = Path(r"C:\Users\Administrator\Desktop")
TARGET = DESKTOP / "123.txt"
TARGET.unlink(missing_ok=True)
Path(r"D:\aiapp\TAGENT\123.txt").unlink(missing_ok=True)

set_clip("123")
subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
time.sleep(1.0)
subprocess.Popen(["notepad"], cwd=str(DESKTOP))   # 关键：CWD=桌面
time.sleep(2.5)
print("前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

combo(0x11, 0x41, tail=0.3)                       # Ctrl+A
combo(0x11, 0x56, tail=0.8)                       # Ctrl+V -> 123
combo(0x11, 0x53, tail=2.0)                       # Ctrl+S -> 另存为(默认目录=桌面)
print("Ctrl+S 后:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
set_clip("123.txt")                               # 剪贴板换文件名
combo(0x11, 0x41, tail=0.3)                       # 文件名框 Ctrl+A
combo(0x11, 0x56, tail=0.5)                       # Ctrl+V -> 123.txt
combo(0x0D, tail=2.0)                             # 回车保存
time.sleep(1.0)
print("保存后:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
print("桌面文件:", TARGET.exists(),
      repr(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else None)
combo(0x12, 0x73)
time.sleep(0.8)
