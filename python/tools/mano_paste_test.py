"""实验：WebView2 收件人框对三种 Ctrl+V 注入方式的响应
A. keybd_event 快速（当前实现）
B. keybd_event 慢速（间隔 150ms）
C. SendInput 带 SCANCODE
每轮：物理点击收件人框 -> set_clip(标记文本) -> 注入 -> PrintWindow+OCR 验证
"""
import ctypes
import time
from ctypes import wintypes

u32 = ctypes.windll.user32
# 线程级物理坐标系（SetProcessDPIAware 在已初始化进程内会静默失败）
u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
gdi = ctypes.windll.gdi32
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1

import pytesseract
from PIL import Image, ImageOps
import win32clipboard
import win32con
import win32gui
import win32process


def set_clip(text):
    win32clipboard.OpenClipboard()
    win32clipboard.CloseClipboard()
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    win32clipboard.CloseClipboard()


def click(x, y):
    u32.SetCursorPos(x, y)
    time.sleep(0.15)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.6)


def paste_fast():
    u32.keybd_event(0x11, 0x1D, 0, 0)
    time.sleep(0.04)
    u32.keybd_event(0x56, 0x2F, 0, 0)
    time.sleep(0.04)
    u32.keybd_event(0x56, 0, 2, 0)
    u32.keybd_event(0x11, 0, 2, 0)
    time.sleep(0.6)


def paste_slow():
    u32.keybd_event(0x11, 0x1D, 0, 0)
    time.sleep(0.15)
    u32.keybd_event(0x56, 0x2F, 0, 0)
    time.sleep(0.12)
    u32.keybd_event(0x56, 0, 2, 0)
    time.sleep(0.1)
    u32.keybd_event(0x11, 0, 2, 0)
    time.sleep(0.8)


class KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class IN(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ki", KI), ("pad", ctypes.c_ubyte * 8)]


def _si(vk, scan, flags):
    ki = KI(vk, scan, flags, 0, None)
    return u32.SendInput(1, ctypes.byref(IN(INPUT_KEYBOARD, ki)), ctypes.sizeof(IN))


def paste_sendinput():
    # Ctrl down / V down / V up / Ctrl up，均带 SCANCODE flag（Chromium 友好）
    _si(0x11, u32.MapVirtualKeyW(0x11, 0), KEYEVENTF_SCANCODE)
    time.sleep(0.05)
    _si(0x56, u32.MapVirtualKeyW(0x56, 0), KEYEVENTF_SCANCODE)
    time.sleep(0.05)
    _si(0x56, u32.MapVirtualKeyW(0x56, 0), KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
    _si(0x11, u32.MapVirtualKeyW(0x11, 0), KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
    time.sleep(0.8)


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


def ocr_to_area(hwnd, w, h):
    img = pw_shot(hwnd, w, h)
    area = img.crop((int(w * 0.2), int(h * 0.06), w, int(h * 0.22)))
    g = ImageOps.autocontrast(ImageOps.grayscale(area))
    g = g.resize((int(g.width * 2), int(g.height * 2)), Image.LANCZOS)
    return pytesseract.image_to_string(g, lang="chi_sim+eng", config="--psm 6")


hwnd = u32.FindWindowW("Outlook Host", None)
rect = win32gui.GetWindowRect(hwnd)
w, h = rect[2] - rect[0], rect[3] - rect[1]

# 找收件人框位置：词级定位，失败则用固定比例（收件人行约在 17% 高度）
img = pw_shot(hwnd, w, h)
g = ImageOps.autocontrast(ImageOps.grayscale(img))
g2 = g.resize((int(w * 1.5), int(h * 1.5)), Image.LANCZOS)
d = pytesseract.image_to_data(g2, lang="chi_sim+eng", config="--psm 6",
                              output_type=pytesseract.Output.DICT)
to_xy = None
for i, txt in enumerate(d["text"]):
    if "收件人" in (txt or ""):
        to_xy = (int((d["left"][i] + d["width"][i]) / 1.5) + 250,
                 int((d["top"][i] + d["height"][i] / 2) / 1.5))
        break
if not to_xy:
    to_xy = (int(w * 0.45), int(h * 0.17))
    print("词定位失败，用固定比例坐标")
print("收件人框点击点:", to_xy)

# 前台化
fg = win32gui.GetForegroundWindow()
_, a = win32process.GetWindowThreadProcessId(fg)
_, b = win32process.GetWindowThreadProcessId(hwnd)
u32.keybd_event(0x12, 0, 0, 0)
u32.keybd_event(0x12, 0, 2, 0)
if a != b:
    u32.AttachThreadInput(a, b, True)
u32.SetForegroundWindow(hwnd)
if a != b:
    u32.AttachThreadInput(a, b, False)
time.sleep(0.8)

for name, fn, tag in (("A keybd_event快速", paste_fast, "AAAA-test-a"),
                      ("B keybd_event慢速", paste_slow, "BBBB-test-b"),
                      ("C SendInput+SCANCODE", paste_sendinput, "CCCC-test-c")):
    click(*to_xy)
    set_clip(tag)
    fn()
    text = ocr_to_area(hwnd, w, h)
    ok = tag[:8].lower() in text.lower()
    print(f"{name}: {'进入 ✓' if ok else '未进入 ✗'} | OCR: {' '.join(text.split())[:100]}")
