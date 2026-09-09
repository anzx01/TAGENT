"""调试19：点击'新建'展开子菜单 -> ROI 裁剪放大后 OCR '文本文档'"""
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
import pytesseract
import win32gui
from PIL import Image
from services.ocr_locator import _find_tesseract, best_lang

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()


def snap():
    for _ in range(3):
        try:
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception:
            time.sleep(0.25)
    return None


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
u32.SetCursorPos(cx, cy)
time.sleep(0.2)
u32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.04)
u32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(0.5)
key(0x10); key(0x79); key(0x79, KEYEVENTF_KEYUP); key(0x10, KEYEVENTF_KEYUP)
time.sleep(1.2)

# 左键点"新建" (98+30, 343+12)
u32.SetCursorPos(128, 355)
time.sleep(0.2)
u32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.04)
u32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(1.5)

img = snap()
# 子菜单 ROI：右侧大范围
roi = img.crop((440, 300, 900, 900))
roi2 = roi.resize((roi.width * 2, roi.height * 2), Image.LANCZOS)
out = Path(tempfile.mkdtemp()) / "sub.png"
roi2.save(out)
for psm in (6, 11):
    data = pytesseract.image_to_data(roi2, lang=best_lang(), config=f"--psm {psm}",
                                     output_type=pytesseract.Output.DICT)
    words = [(t, l, tp) for t, l, tp in zip(data["text"], data["left"], data["top"]) if t.strip()]
    print(f"psm{psm}:", words)

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
