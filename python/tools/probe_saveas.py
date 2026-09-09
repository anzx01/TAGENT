"""调试26：另存为对话框全词表 dump"""
import ctypes
import subprocess
import sys
import tempfile
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


import mss
import pytesseract
import win32gui
from PIL import Image
from services.ocr_locator import _find_tesseract, best_lang

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()

subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
time.sleep(1.0)
subprocess.Popen(["notepad"])
time.sleep(2.5)
print("前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

key(0x31); key(0x31, KEYEVENTF_KEYUP)   # 输入 1
time.sleep(0.5)
key(0x11); key(0x53); key(0x53, KEYEVENTF_KEYUP); key(0x11, KEYEVENTF_KEYUP)  # Ctrl+S
time.sleep(2.0)
print("前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

with mss.mss() as sct:
    shot = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
out = Path(tempfile.mkdtemp()) / "saveas.png"
img.save(out)
print("截图:", out)
data = pytesseract.image_to_data(img, lang=best_lang(), config="--psm 6",
                                 output_type=pytesseract.Output.DICT)
words = [(t, l, tp) for t, l, tp in zip(data["text"], data["left"], data["top"]) if t.strip()]
print("全词表:")
for w in words:
    print("  ", w)
