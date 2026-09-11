"""清理 Outlook 残留：Esc 错误弹窗 -> 关闭编辑视图（放弃草稿）-> 回收件箱"""
import ctypes
import time

u32 = ctypes.windll.user32
u32.SetProcessDPIAware()
KEYEVENTF_KEYUP = 0x0002


def key(vk, flags=0):
    u32.keybd_event(vk, u32.MapVirtualKeyW(vk, 0), flags, 0)


def combo(*vks, tail=0.5):
    for vk in vks:
        key(vk)
        time.sleep(0.06)
    for vk in reversed(vks):
        key(vk, KEYEVENTF_KEYUP)
        time.sleep(0.06)
    time.sleep(tail)


import win32gui
import win32process

hwnd = u32.FindWindowW("Outlook Host", None)
print("标题:", win32gui.GetWindowText(hwnd))
fg = win32gui.GetForegroundWindow()
_, a = win32process.GetWindowThreadProcessId(fg)
_, b = win32process.GetWindowThreadProcessId(hwnd)
if a != b:
    u32.AttachThreadInput(a, b, True)
u32.SetForegroundWindow(hwnd)
if a != b:
    u32.AttachThreadInput(a, b, False)
time.sleep(0.6)

# 1) Esc 关错误提示
combo(0x1B, tail=0.8)
# 2) 关编辑视图（Ctrl+W 或 Esc 出放弃提示）
combo(0x11, 0x57, tail=1.2)   # Ctrl+W 关闭标签
# 3) 若弹「保存草稿?」提示，Tab 到放弃/Del 键确认丢弃
combo(0x1B, tail=0.5)
time.sleep(1.0)
print("清理后标题:", win32gui.GetWindowText(hwnd))
