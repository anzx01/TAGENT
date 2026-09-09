"""调试22：Win+R -> 输入 notepad -> 截图看运行框内容"""
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
from PIL import Image


def snap():
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


key(0x5B, KEYEVENTF_EXTENDEDKEY); key(0x52); key(0x52, KEYEVENTF_KEYUP)
key(0x5B, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
time.sleep(1.2)
print("前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

for ch in "notepad":
    vk = ord(ch.upper())
    key(vk)
    key(vk, KEYEVENTF_KEYUP)
    time.sleep(0.1)
time.sleep(0.8)
img = snap()
out = Path(tempfile.mkdtemp()) / "run.png"
img.save(out)
print("截图:", out)

import pytesseract
from services.ocr_locator import _find_tesseract, best_lang
pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
data = pytesseract.image_to_data(img, lang=best_lang(), config="--psm 6",
                                 output_type=pytesseract.Output.DICT)
words = [(t, l, tp) for t, l, tp in zip(data["text"], data["left"], data["top"])
         if t.strip() and tp < 600]
print("上半屏词:", words[:25])
