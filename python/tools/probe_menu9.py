"""调试9：到桌面 -> 右键 -> 同时用 OCR 词表 + UIA 枚举菜单项"""
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
import uiautomation as ua
import win32gui
from PIL import Image
from services.ocr_locator import _find_tesseract, best_lang

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()


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


assert win_d(), "未到桌面"
cx, cy = SW // 2, SH // 2
u32.SetCursorPos(cx, cy)
time.sleep(0.3)
u32.mouse_event(0x0008, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0010, 0, 0, 0, 0)
time.sleep(1.5)

# --- OCR 词表 ---
with mss.mss() as sct:
    shot = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
crop = img.crop((cx, cy, cx + 800, cy + 700))
data = pytesseract.image_to_data(crop, lang=best_lang(), config="--psm 6",
                                 output_type=pytesseract.Output.DICT)
words = [(t, l, tp) for t, l, tp in zip(data["text"], data["left"], data["top"]) if t.strip()]
print("OCR 词表(前25):", words[:25])

# --- UIA 枚举 ---
print("--- UIA 菜单枚举 ---")
try:
    menu = ua.GetFocusedControl()
    seen = 0
    ctrl = menu
    for _ in range(6):  # 向上找菜单根
        if ctrl is None:
            break
        if ctrl.ControlTypeName in ("MenuControl", "ContextMenu") or "menu" in (ctrl.ClassName or "").lower():
            break
        ctrl = ctrl.GetParentControl()
    root = ctrl or menu
    print("菜单根:", root.ControlTypeName, root.ClassName, repr(root.Name))
    for item, depth in root.WalkControl(maxDepth=6):
        if seen > 40:
            break
        name = (item.Name or "").strip()
        if name:
            r = item.BoundingRectangle
            print(f"  {'  ' * depth}{item.ControlTypeName} {name!r} rect=({r.left},{r.top},{r.right},{r.bottom})")
            seen += 1
except Exception as e:
    print("UIA 枚举失败:", e)

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
