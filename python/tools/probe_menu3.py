"""调试3：点击任务栏'显示桌面'按钮（屏幕右下角）-> 验证前台变桌面 -> 右键 -> OCR"""
import ctypes
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mss
import pytesseract
import win32gui
from PIL import Image
from services.ocr_locator import _find_tesseract, best_lang

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
u32 = ctypes.windll.user32
SW, SH = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
print("屏幕:", SW, "x", SH)

u32.SetCursorPos(SW - 6, SH - 25)   # 任务栏最右缘"显示桌面"条
time.sleep(0.3)
u32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(1.2)
print("前台窗口:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))

u32.SetCursorPos(960, 540)
time.sleep(0.3)
u32.mouse_event(0x0008, 0, 0, 0, 0)
time.sleep(0.05)
u32.mouse_event(0x0010, 0, 0, 0, 0)
time.sleep(1.2)

with mss.mss() as sct:
    shot = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
out = Path(tempfile.mkdtemp()) / "menu3.png"
img.save(out)
print("截图:", out)

crop = img.crop((940, 520, 1400, 900))
data = pytesseract.image_to_data(crop, lang=best_lang(), config="--psm 6",
                                 output_type=pytesseract.Output.DICT)
for t, c, l, tp in zip(data["text"], data["conf"], data["left"], data["top"]):
    if t.strip():
        print(f"  {t!r} conf={c} at crop({l},{tp})")

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
