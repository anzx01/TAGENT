"""重启 Outlook 到干净的新邮件编辑视图（供粘贴实验）"""
import ctypes
import os
import subprocess
import time

import win32gui
import win32process

u32 = ctypes.windll.user32
u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
KEYEVENTF_KEYUP = 0x0002

subprocess.run(["taskkill", "/IM", "olk.exe", "/F"], capture_output=True)
time.sleep(1.5)
os.startfile(r"shell:AppsFolder\Microsoft.OutlookForWindows_8wekyb3d8bbwe"
             r"!Microsoft.OutlookforWindows")
hwnd = 0
for _ in range(45):
    time.sleep(1)
    hwnd = u32.FindWindowW("Outlook Host", None)
    if hwnd and win32gui.IsWindowVisible(hwnd):
        break
time.sleep(5)

# 前台 + 最大化
import win32con
u32.keybd_event(0x12, 0, 0, 0)
u32.keybd_event(0x12, 0, 2, 0)
u32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
fg = win32gui.GetForegroundWindow()
_, a = win32process.GetWindowThreadProcessId(fg)
_, b = win32process.GetWindowThreadProcessId(hwnd)
if a != b:
    u32.AttachThreadInput(a, b, True)
u32.SetForegroundWindow(hwnd)
if a != b:
    u32.AttachThreadInput(a, b, False)
time.sleep(1.5)
print("标题:", win32gui.GetWindowText(hwnd))

# Ctrl+N 新邮件
u32.keybd_event(0x11, 0x1D, 0, 0)
time.sleep(0.06)
u32.keybd_event(0x4E, 0x31, 0, 0)
time.sleep(0.06)
u32.keybd_event(0x4E, 0, 2, 0)
u32.keybd_event(0x11, 0, 2, 0)
time.sleep(4)
print("Ctrl+N 后标题:", win32gui.GetWindowText(hwnd))
r = win32gui.GetWindowRect(hwnd)
print("rect(物理):", r, "size:", r[2] - r[0], r[3] - r[1])
