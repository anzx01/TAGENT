"""把 Outlook 置前（为视觉接管任务准备）"""
import ctypes
import time

import win32gui
import win32process

u32 = ctypes.windll.user32
u32.SetProcessDPIAware()
hwnd = u32.FindWindowW("Outlook Host", None)
if not hwnd:
    import os
    os.startfile(r"shell:AppsFolder\Microsoft.OutlookForWindows_8wekyb3d8bbwe"
                 r"!Microsoft.OutlookforWindows")
    for _ in range(30):
        time.sleep(1)
        hwnd = u32.FindWindowW("Outlook Host", None)
        if hwnd and win32gui.IsWindowVisible(hwnd):
            break
    time.sleep(4)   # 等收件箱视图就绪
print("Outlook hwnd:", hwnd, "标题:", win32gui.GetWindowText(hwnd) if hwnd else None)
if hwnd:
    fg = win32gui.GetForegroundWindow()
    _, a = win32process.GetWindowThreadProcessId(fg)
    _, b = win32process.GetWindowThreadProcessId(hwnd)
    u32.keybd_event(0x12, 0, 0, 0)
    u32.keybd_event(0x12, 0, 0x0002, 0)
    if a != b:
        u32.AttachThreadInput(a, b, True)
    u32.ShowWindow(hwnd, 9)
    u32.SetForegroundWindow(hwnd)
    if a != b:
        u32.AttachThreadInput(a, b, False)
    time.sleep(0.6)
    print("前台:", win32gui.GetWindowText(win32gui.GetForegroundWindow()))
