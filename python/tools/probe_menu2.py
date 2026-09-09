"""调试2：Win+D -> 右键中心 -> 截屏，输出菜单区域(900-1300, 500-800)词表"""
import ctypes
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mss
import pytesseract
from PIL import Image
from services.ocr_locator import _find_tesseract, best_lang

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
u32 = ctypes.windll.user32

# Win+D (SendInput 更可靠)
u32.keybd_event(0x5B, 0, 0, 0)
time.sleep(0.06)
u32.keybd_event(0x44, 0, 0, 0)
time.sleep(0.06)
u32.keybd_event(0x44, 0, 2, 0)
u32.keybd_event(0x5B, 0, 2, 0)
time.sleep(1.5)

# 验证前台窗口标题（应为 Program Manager/桌面）
import win32gui
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
out = Path(tempfile.mkdtemp()) / "menu2.png"
img.save(out)
print("截图:", out)

# 菜单应在右键点右下方：裁剪 (940, 520) - (1400, 900)
crop = img.crop((940, 520, 1400, 900))
data = pytesseract.image_to_data(crop, lang=best_lang(), config="--psm 6",
                                 output_type=pytesseract.Output.DICT)
for t, c, l, tp in zip(data["text"], data["conf"], data["left"], data["top"]):
    if t.strip():
        print(f"  {t!r} conf={c} at crop({l},{tp})")

u32.keybd_event(0x1B, 0, 0, 0)
u32.keybd_event(0x1B, 0, 2, 0)
