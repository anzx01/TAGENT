"""调试4：SetProcessDPIAware 后全物理坐标：显示桌面 -> 右键中心 -> OCR 菜单"""
import ctypes
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32
SW, SH = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
print("物理屏幕:", SW, "x", SH)

import mss
import pytesseract
import win32gui
from PIL import Image
from services.ocr_locator import _find_tesseract, best_lang, capture_and_find

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()

# 显示桌面
u32.SetCursorPos(SW - 6, SH - 25)
time.sleep(0.3)
u32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(1.2)
print("前台窗口:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

# 右键中心
cx, cy = SW // 2, SH // 2
u32.SetCursorPos(cx, cy)
time.sleep(0.3)
u32.mouse_event(0x0008, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0010, 0, 0, 0, 0)
time.sleep(1.2)

pos = capture_and_find("新建")
print("OCR 找'新建':", pos)

with mss.mss() as sct:
    shot = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
out = Path(tempfile.mkdtemp()) / "menu4.png"
img.save(out)
print("截图:", out, "尺寸:", img.size)
crop = img.crop((cx, cy, cx + 500, cy + 400))
data = pytesseract.image_to_data(crop, lang=best_lang(), config="--psm 6",
                                 output_type=pytesseract.Output.DICT)
for t, c, l, tp in zip(data["text"], data["conf"], data["left"], data["top"]):
    if t.strip():
        print(f"  {t!r} conf={c} at ({l},{tp})")

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
