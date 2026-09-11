"""对照组：记事本 + 三种粘贴方式（隔离 Outlook WebView2 特有问题）"""
import ctypes
import subprocess
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


def set_clip(text):
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    win32clipboard.CloseClipboard()


def get_clip():
    try:
        win32clipboard.OpenClipboard()
        d = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return d
    except Exception as e:
        return f"<err {e}>"


def click(x, y):
    u32.SetCursorPos(x, y)
    time.sleep(0.15)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.5)


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


def ocr_img(img):
    g = ImageOps.autocontrast(ImageOps.grayscale(img))
    g = g.resize((int(g.width * 1.5), int(g.height * 1.5)), Image.LANCZOS)
    return pytesseract.image_to_string(g, lang="eng", config="--psm 6")


# 准备记事本（清会话）
import shutil
from pathlib import Path
subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
time.sleep(1)
base = Path(r"C:\Users\Administrator\AppData\Local\Packages"
            r"\Microsoft.WindowsNotepad_8wekyb3d8bbwe\LocalState")
for sub in ("TabState", "WindowState"):
    shutil.rmtree(base / sub, ignore_errors=True)
subprocess.Popen(["notepad"])
time.sleep(3)

hwnd = None
res = []
win32gui.EnumWindows(lambda h, _: res.append(h)
                     if (win32gui.IsWindowVisible(h)
                         and "Notepad" in (win32gui.GetWindowText(h) or "")) else None, None)
hwnd = res[0]
rect = win32gui.GetWindowRect(hwnd)
w, h = rect[2] - rect[0], rect[3] - rect[1]
print("notepad:", hwnd, "rect(物理):", rect)
# 前台化
u32.keybd_event(0x12, 0, 0, 0)
u32.keybd_event(0x12, 0, 2, 0)
u32.SetForegroundWindow(hwnd)
time.sleep(0.8)

# 点击编辑区中心
click(int(w * 0.5), int(h * 0.6))
for name, fn, tag in (("A keybd_event快速", paste_fast, "AAAA-test-a"),
                      ("B keybd_event慢速", paste_slow, "BBBB-test-b"),
                      ("C SendInput+SCANCODE", paste_sendinput, "CCCC-test-c")):
    set_clip(tag)
    print(f"剪贴板({name}):", get_clip()[:20])
    fn()
    img = pw_shot(hwnd, w, h)
    text = ocr_img(img)
    ok = tag[:8] in text
    print(f"{name}: {'进入 ✓' if ok else '未进入 ✗'} | OCR: {' '.join(text.split())[:80]}")
