"""调试6：桌面右键后，打印菜单区域词表（中心右下 700x600）"""
import ctypes
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32
SW, SH = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)

import mss
import pytesseract
import win32gui
from PIL import Image
from services.ocr_locator import _find_tesseract, best_lang

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()


def foreground():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


def click(x, y, delay=0.5):
    u32.SetCursorPos(x, y)
    time.sleep(0.15)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(delay)


click(SW - 4, SH - 40, 1.0)
print("前台:", repr(foreground()))

cx, cy = SW // 2, SH // 2
u32.SetCursorPos(cx, cy)
time.sleep(0.3)
u32.mouse_event(0x0008, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0010, 0, 0, 0, 0)
time.sleep(1.5)

with mss.mss() as sct:
    shot = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
out = Path(tempfile.mkdtemp()) / "menu6.png"
img.save(out)
print("截图:", out)

crop = img.crop((cx, cy, cx + 700, cy + 600))
crop.save(out.parent / "menu6_crop.png")
data = pytesseract.image_to_data(crop, lang=best_lang(), config="--psm 6",
                                 output_type=pytesseract.Output.DICT)
found = False
for t, c, l, tp in zip(data["text"], data["conf"], data["left"], data["top"]):
    if t.strip():
        print(f"  {t!r} conf={c} ({l},{tp})")
        if "新" in t or "建" in t:
            found = True
print("含'新建'字样:", found)

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
