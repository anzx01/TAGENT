"""调试21：录制中 Win+R（对照 hook 是否干扰）"""
import ctypes
import json
import sys
import time
import urllib.request
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

API = "http://127.0.0.1:18899"


def post(url, data=b"{}"):
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def fg():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


def find_window(sub):
    res = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and sub in win32gui.GetWindowText(h):
            res.append(win32gui.GetWindowText(h))
        return True
    win32gui.EnumWindows(cb, None)
    return res


print("录制前前台:", repr(fg()))
s = post(API + "/record/start")
print("录制:", s.get("recording_id"))
time.sleep(0.6)

key(0x5B, KEYEVENTF_EXTENDEDKEY)
time.sleep(0.06)
key(0x52)
time.sleep(0.06)
key(0x52, KEYEVENTF_KEYUP)
key(0x5B, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
time.sleep(1.5)
print("录制中 Win+R 后前台:", repr(fg()), "| 运行窗口:", find_window("运行"))

# 关运行框
u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
time.sleep(0.5)
r = post(API + "/record/stop")
print("停止:", r.get("event_count"), "步")
