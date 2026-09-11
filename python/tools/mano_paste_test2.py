"""Outlook 粘贴实验 v2：以「抄送」词锚定收件人行（收件人框在其左侧）"""
import ctypes
import time
from ctypes import wintypes

u32 = ctypes.windll.user32
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
    _si(0x11, u32.MapVirtualKeyW(0x11, 0), KEYEVENTF_SCANCODE)
    time.sleep(0.06)
    _si(0x56, u32.MapVirtualKeyW(0x56, 0), KEYEVENTF_SCANCODE)
    time.sleep(0.06)
    _si(0x56, u32.MapVirtualKeyW(0x56, 0), KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
    _si(0x11, u32.MapVirtualKeyW(0x11, 0), KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
    time.sleep(0.9)


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


def words(img, scale=2):
    g = ImageOps.autocontrast(ImageOps.grayscale(img))
    g = g.resize((int(g.width * scale), int(g.height * scale)), Image.LANCZOS)
    d = pytesseract.image_to_data(g, lang="chi_sim+eng", config="--psm 6",
                                  output_type=pytesseract.Output.DICT)
    out = []
    for i, txt in enumerate(d["text"]):
        t = (txt or "").strip()
        if t:
            out.append((int((d["top"][i] + d["height"][i] / 2) / scale),
                        int((d["left"][i] + d["width"][i] / 2) / scale), t))
    return out


hwnd = u32.FindWindowW("Outlook Host", None)
rect = win32gui.GetWindowRect(hwnd)
w, h = rect[2] - rect[0], rect[3] - rect[1]
print("窗口:", hwnd, "物理尺寸:", w, h)

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

img = pw_shot(hwnd, w, h)
ws = words(img)
cc = None
for y, x, t in ws:
    if "抄送" in t:
        cc = (x, y)
        print("「抄送」锚点:", cc, repr(t))
        break
assert cc, "抄送锚点未找到（编辑视图不在？）"

# 收件人输入框：抄送左侧同水平线
to_xy = (cc[0] - 380, cc[1])
print("收件人点击点:", to_xy)
click(*to_xy)

# 粘贴实验（两种方式）
for name, fn, tag in (("A keybd_event", paste_fast, "AAAA-test-a"),
                      ("C SendInput", paste_sendinput, "CCCC-test-c")):
    set_clip(tag)
    fn()
    img2 = pw_shot(hwnd, w, h)
    # OCR 收件人行区域（抄送 y 上下 ±60，全宽右半）
    band = img2.crop((cc[0] - 600, max(0, cc[1] - 60), w, cc[1] + 60))
    g = ImageOps.autocontrast(ImageOps.grayscale(band))
    g = g.resize((int(g.width * 2), int(g.height * 2)), Image.LANCZOS)
    text = pytesseract.image_to_string(g, lang="chi_sim+eng", config="--psm 6")
    flat = " ".join(text.split())
    ok = tag[:8] in flat
    print(f"{name}: {'进入 ✓' if ok else '未进入 ✗'} | OCR: {flat[:110]}")
