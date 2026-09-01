"""输入注入引擎 V3 子包公共定义：常量、结果类型、前台守卫"""
import ctypes
import logging
import time
from dataclasses import dataclass
from typing import Tuple

import win32gui
import win32process

log = logging.getLogger("input_engine")

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
EM_REPLACESEL = 0x00C2
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002

EDIT_CLASSES = ("Edit", "RichEditD2DPT", "RichEdit20W", "RICHEDIT50W")
RELEASE_MODIFIERS = [0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C]

VK_MAP: dict = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
}
for _i in range(12):
    VK_MAP[f"f{_i + 1}"] = 0x70 + _i

CHAR_INTERVAL = 0.03


@dataclass
class StepResult:
    """单步注入结果：ok=False 时 reason 为失败原因（对齐 winappCli fail-fast）"""
    ok: bool
    method: str
    reason: str = ""


def release_keys(vks) -> None:
    u32 = ctypes.windll.user32
    for vk in vks:
        u32.keybd_event(vk, 0, 2, 0)


def force_foreground(hwnd: int) -> bool:
    """强制目标窗口到前台（ALT trick + AttachThreadInput + 验证）"""
    try:
        if not win32gui.IsWindow(hwnd):
            return False
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True
        u32 = ctypes.windll.user32
        _, fg_tid = win32process.GetWindowThreadProcessId(fg)
        _, target_tid = win32process.GetWindowThreadProcessId(hwnd)
        u32.keybd_event(0x12, 0, 0, 0)
        u32.keybd_event(0x12, 0, 2, 0)
        if fg_tid != target_tid:
            u32.AttachThreadInput(fg_tid, target_tid, True)
        win32gui.ShowWindow(hwnd, 9)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        if fg_tid != target_tid:
            u32.AttachThreadInput(fg_tid, target_tid, False)
        time.sleep(0.4)
        u32.keybd_event(0x12, 0, 2, 0)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


class ForegroundGuard:
    """前台守卫：注入前确认目标窗口在前台，失败快速返回而非打错窗口"""

    def ensure(self, hwnd: int, retries: int = 3) -> Tuple[bool, str]:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False, "invalid_hwnd"
        if win32gui.GetForegroundWindow() == hwnd:
            return True, ""
        for _ in range(retries):
            if force_foreground(hwnd):
                return True, ""
            time.sleep(0.3)
        if win32gui.GetForegroundWindow() == hwnd:
            return True, ""
        return False, f"foreground_not_target (fg={win32gui.GetForegroundWindow()}, target={hwnd})"
