"""调试10：右键桌面后 0.3s/1.2s 双截图 + 像素方差判断菜单是否弹出"""
import ctypes
import sys
import tempfile
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


import mss
import win32gui
from PIL import Image, ImageStat


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


assert win_d()
cx, cy = SW // 2, SH // 2
print("右键点:", cx, cy)

# 鼠标下窗口
u32.SetCursorPos(cx, cy)
time.sleep(0.3)
hwnd = u32.WindowFromPoint(ctypes.wintypes.POINT(cx, cy) if False else None) if False else None
import ctypes.wintypes
pt = ctypes.wintypes.POINT(cx, cy)
hwnd = u32.WindowFromPoint(pt)
print("鼠标下窗口:", repr(win32gui.GetWindowText(hwnd)), win32gui.GetClassName(hwnd))

before = None
with mss.mss() as sct:
    def snap():
        shot = sct.grab(sct.monitors[1])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    before = snap()

u32.mouse_event(0x0008, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0010, 0, 0, 0, 0)
time.sleep(0.3)
early = snap()
time.sleep(0.9)
late = snap()

for name, im in (("before", before), ("early", early), ("late", late)):
    region = im.crop((cx, cy, cx + 400, cy + 300))
    st = ImageStat.Stat(region.convert("L"))
    print(f"{name}: 均值={st.mean[0]:.1f} 方差={st.stddev[0]:.1f}")

tmp = Path(tempfile.mkdtemp())
late.save(tmp / "late.png")
early.save(tmp / "early.png")
print("截图保存于:", tmp)

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
