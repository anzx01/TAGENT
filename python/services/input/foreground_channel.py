"""前台物理通道：最后兜底，必须通过前台守卫（winappCli foreground_not_target）"""
import ctypes
import logging
import time
from typing import Callable, Optional

import win32process

from services.input.common import CHAR_INTERVAL, RELEASE_MODIFIERS, StepResult, \
    ForegroundGuard, release_keys

log = logging.getLogger("input_engine")


class ForegroundChannel:
    def __init__(self):
        self.guard = ForegroundGuard()

    def click(self, hwnd: int, x: int, y: int) -> StepResult:
        ok, reason = self.guard.ensure(hwnd)
        if not ok:
            return StepResult(False, "foreground_click", reason)
        self._mouse_click(x, y)
        return StepResult(True, "foreground_click")

    def right_click(self, hwnd: int, x: int, y: int) -> StepResult:
        ok, reason = self.guard.ensure(hwnd)
        if not ok:
            return StepResult(False, "foreground_right_click", reason)
        self._mouse_right_click(x, y)
        time.sleep(0.5)
        return StepResult(True, "foreground_right_click")

    def type_text(self, hwnd: int, text: str) -> StepResult:
        ok, reason = self.guard.ensure(hwnd)
        if not ok:
            return StepResult(False, "foreground_type", reason)
        u32 = ctypes.windll.user32
        _, target_tid = win32process.GetWindowThreadProcessId(hwnd)
        current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = False
        if target_tid != current_tid:
            try:
                u32.AttachThreadInput(current_tid, target_tid, True)
                attached = True
            except Exception:
                pass
        try:
            for char in text:
                if char == "\n":
                    u32.keybd_event(0x0D, u32.MapVirtualKeyW(0x0D, 0), 0, 0)
                    u32.keybd_event(0x0D, 0, 2, 0)
                elif char == "\t":
                    u32.keybd_event(0x09, u32.MapVirtualKeyW(0x09, 0), 0, 0)
                    u32.keybd_event(0x09, 0, 2, 0)
                else:
                    vk_scan = u32.VkKeyScanW(ord(char))
                    vk = vk_scan & 0xFF
                    shift = (vk_scan >> 8) & 0x01
                    scan = u32.MapVirtualKeyW(vk, 0)
                    if shift:
                        u32.keybd_event(0x10, 0, 0, 0)
                    u32.keybd_event(vk, scan, 0, 0)
                    u32.keybd_event(vk, scan, 2, 0)
                    if shift:
                        u32.keybd_event(0x10, 0, 2, 0)
                time.sleep(CHAR_INTERVAL)
            return StepResult(True, "foreground_type")
        finally:
            if attached:
                try:
                    u32.AttachThreadInput(current_tid, target_tid, False)
                except Exception:
                    pass
            release_keys(RELEASE_MODIFIERS)

    def key_combo(self, hwnd: int, vks: list) -> StepResult:
        ok, reason = self.guard.ensure(hwnd)
        if not ok:
            return StepResult(False, "foreground_key_combo", reason)
        u32 = ctypes.windll.user32
        for vk in vks:
            u32.keybd_event(vk, u32.MapVirtualKeyW(vk, 0), 0, 0)
            time.sleep(0.06)
        for vk in reversed(vks):
            u32.keybd_event(vk, 0, 2, 0)
            time.sleep(0.06)
        release_keys(RELEASE_MODIFIERS)
        time.sleep(0.8)
        return StepResult(True, "foreground_key_combo")

    def scroll(self, x: int, y: int, delta: int) -> None:
        u32 = ctypes.windll.user32
        u32.SetCursorPos(x, y)
        time.sleep(0.05)
        u32.mouse_event(0x0800, 0, 0, delta, 0)

    def drag(self, sx: int, sy: int, ex: int, ey: int,
             stopped: Optional[Callable[[], bool]] = None) -> None:
        u32 = ctypes.windll.user32
        u32.SetCursorPos(sx, sy)
        time.sleep(0.05)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        for i in range(1, 11):
            if stopped and stopped():
                break
            u32.SetCursorPos(int(sx + (ex - sx) * i / 10),
                             int(sy + (ey - sy) * i / 10))
            time.sleep(0.03)
        u32.mouse_event(0x0004, 0, 0, 0, 0)

    def _mouse_click(self, x: int, y: int) -> None:
        u32 = ctypes.windll.user32
        u32.SetCursorPos(x, y)
        time.sleep(0.1)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.03)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.2)

    def _mouse_double_click(self, x: int, y: int) -> None:
        u32 = ctypes.windll.user32
        u32.SetCursorPos(x, y)
        time.sleep(0.1)
        for _ in range(2):
            u32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.03)
            u32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.08)
        time.sleep(0.2)

    def _mouse_right_click(self, x: int, y: int) -> None:
        u32 = ctypes.windll.user32
        u32.SetCursorPos(x, y)
        time.sleep(0.1)
        u32.mouse_event(0x0008, 0, 0, 0, 0)
        time.sleep(0.03)
        u32.mouse_event(0x0010, 0, 0, 0, 0)
        time.sleep(0.2)
