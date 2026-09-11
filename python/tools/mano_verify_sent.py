"""验证：点击左栏「已发送邮件」-> OCR 最新一封"""
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
import win32process


def force_fg(hd):
    fg = win32gui.GetForegroundWindow()
    if fg == hd:
        return
    _, a = win32process.GetWindowThreadProcessId(fg)
    _, b = win32process.GetWindowThreadProcessId(hd)
    u32.keybd_event(0x12, 0, 0, 0)
    u32.keybd_event(0x12, 0, 0x0002, 0)
    if a != b:
        u32.AttachThreadInput(a, b, True)
    u32.SetForegroundWindow(hd)
    if a != b:
        u32.AttachThreadInput(a, b, False)
    time.sleep(0.5)


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


def find_word(img, kw, scale=3):
    g = ImageOps.autocontrast(ImageOps.grayscale(img))
    g = g.resize((int(g.width * scale), int(g.height * scale)), Image.LANCZOS)
    d = pytesseract.image_to_data(g, lang="chi_sim+eng", config="--psm 6",
                                  output_type=pytesseract.Output.DICT)
    for i, txt in enumerate(d["text"]):
        if kw in (txt or ""):
            return (int((d["left"][i] + d["width"][i] / 2) / scale),
                    int((d["top"][i] + d["height"][i] / 2) / scale))
    return None


hwnd = u32.FindWindowW("Outlook Host", None)
rect = win32gui.GetWindowRect(hwnd)
w, h = rect[2] - rect[0], rect[3] - rect[1]
print("标题:", win32gui.GetWindowText(hwnd))
force_fg(hwnd)
img = pw_shot(hwnd, w, h)
pos = find_word(img.crop((0, int(h * 0.08), int(w * 0.17), h)), "已发送")
print("已发送文件夹:", pos)
if pos:
    x, y = pos
    u32.SetCursorPos(x, y + int(h * 0.08))
    time.sleep(0.2)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(4)
    force_fg(hwnd)
    img2 = pw_shot(hwnd, w, h)
    area = img2.crop((int(w * 0.17), int(h * 0.1), w, int(h * 0.5)))
    g = ImageOps.autocontrast(ImageOps.grayscale(area))
    g = g.resize((int(g.width * 1.6), int(g.height * 1.6)), Image.LANCZOS)
    text = pytesseract.image_to_string(g, lang="chi_sim+eng", config="--psm 6")
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    print("== 已发送列表 ==")
    for l in lines[:14]:
        print("  ", l[:135])
