"""全窗口 OCR 看当前 Outlook 实际状态"""
import sys
import time

sys.path.insert(0, ".")
import ctypes

from ctypes import wintypes
u32 = ctypes.windll.user32
u32.SetProcessDPIAware()
gdi = ctypes.windll.gdi32

import pytesseract
from PIL import Image, ImageOps
import win32gui


def pw_shot(hwnd, w, h):
    hdc = u32.GetWindowDC(hwnd)
    memdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(memdc, bmp)
    u32.PrintWindow(hwnd, memdc, 2)

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BMI(ctypes.Structure):
        _fields_ = [("bmiHeader", BMIH), ("biColors", wintypes.DWORD * 3)]

    bmi = BMI()
    bmi.bmiHeader.biSize = ctypes.sizeof(BMIH)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(memdc)
    u32.ReleaseDC(hwnd, hdc)
    return img


hwnd = u32.FindWindowW("Outlook Host", None)
rect = win32gui.GetWindowRect(hwnd)
w, h = rect[2] - rect[0], rect[3] - rect[1]
img = pw_shot(hwnd, w, h)
img.save(r"d:\aiapp\TAGENT\python\tools\mano_state.png")
g = ImageOps.autocontrast(ImageOps.grayscale(img))
g = g.resize((int(g.width * 1.3), int(g.height * 1.3)), Image.LANCZOS)
text = pytesseract.image_to_string(g, lang="chi_sim+eng", config="--psm 3")
lines = [l.rstrip() for l in text.splitlines() if l.strip() and len(l.strip()) > 2]
print(f"== 全窗 OCR（{len(lines)} 行）==")
for l in lines[:30]:
    print(" ", l[:150])
