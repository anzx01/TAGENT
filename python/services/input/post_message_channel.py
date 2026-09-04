"""PostMessage 后台通道：不依赖前台、不抢鼠标键盘（ScreenClaw 路线）"""
import ctypes
import ctypes.wintypes
import logging
import time
from typing import Optional

import win32gui

from services.input.common import EM_REPLACESEL, MK_LBUTTON, MK_RBUTTON, \
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
EM_SETSEL = 0x00B1

log = logging.getLogger("input_engine")


class PostMessageChannel:
    def click(self, hwnd: int, screen_x: int, screen_y: int) -> bool:
        try:
            cx, cy = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
            lparam = (cy << 16) | (cx & 0xFFFF)
            u32 = ctypes.windll.user32
            u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
            time.sleep(0.05)
            u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
            return True
        except Exception:
            return False

    def right_click(self, hwnd: int, screen_x: int, screen_y: int) -> bool:
        try:
            cx, cy = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
            lparam = (cy << 16) | (cx & 0xFFFF)
            u32 = ctypes.windll.user32
            u32.PostMessageW(hwnd, WM_RBUTTONDOWN, MK_RBUTTON, lparam)
            time.sleep(0.05)
            u32.PostMessageW(hwnd, WM_RBUTTONUP, 0, lparam)
            return True
        except Exception:
            return False

    def replace_sel(self, hwnd: int, text: str) -> bool:
        """EM_REPLACESEL 后台输入原生 Edit 控件（Win10 记事本/老对话框）"""
        try:
            edit = self._find_edit_child(hwnd)
            if not edit:
                return False
            buf = ctypes.create_unicode_buffer(text)
            # SendMessageW can block forever if a target UI thread is hung.
            # Use the timeout variant so replay can fall back or hand off.
            result = ctypes.wintypes.DWORD()
            sent = ctypes.windll.user32.SendMessageTimeoutW(
                edit, EM_REPLACESEL, 1, buf,
                0x0002 | 0x0001, 1000, ctypes.byref(result))
            return bool(sent)
        except Exception:
            return False

    def select_all(self, hwnd: int) -> bool:
        """Select the complete contents of a native Edit/RichEdit child.

        Posting a Ctrl+A pair is not reliable across process boundaries: some
        controls insert the literal ``a`` because posted key messages do not
        update the target thread's modifier state.  EM_SETSEL is the native,
        synchronous equivalent and composes safely with the following
        EM_REPLACESEL call.
        """
        try:
            edit = self._find_edit_child(hwnd)
            if not edit:
                return False
            result = ctypes.wintypes.DWORD()
            sent = ctypes.windll.user32.SendMessageTimeoutW(
                edit, EM_SETSEL, 0, -1, 0x0002 | 0x0001, 1000,
                ctypes.byref(result))
            return bool(sent)
        except Exception:
            return False

    def key_press(self, hwnd: int, vks) -> bool:
        """Send a key/combo to a native child without stealing foreground.

        ``vks`` may be one virtual-key integer (the common Enter path) or a
        list for a modifier combo.  PostMessage is non-blocking and therefore
        remains usable when the top-level window cannot be foregrounded.
        """
        try:
            target = self._find_edit_child(hwnd)
            if not target or not win32gui.IsWindow(target):
                return False
            if isinstance(vks, int):
                vks = [vks]
            vks = list(vks or [])
            if not vks:
                return False
            u32 = ctypes.windll.user32
            sent = False
            for vk in vks:
                scan = u32.MapVirtualKeyW(vk, 0)
                # lParam layout: repeat count (low word), scan code (bits
                # 16-23).  Passing the scan code in the low word makes Win32
                # controls interpret it as dozens of repeated key presses.
                down_lparam = 1 | ((scan & 0xFF) << 16)
                sent = bool(u32.PostMessageW(target, WM_KEYDOWN, vk, down_lparam)) or sent
            for vk in reversed(vks):
                scan = u32.MapVirtualKeyW(vk, 0)
                up_lparam = 1 | ((scan & 0xFF) << 16) | 0xC0000000
                sent = bool(u32.PostMessageW(target, WM_KEYUP, vk, up_lparam)) or sent
            return sent
        except Exception:
            return False

    def _find_edit_child(self, hwnd: int) -> Optional[int]:
        found: list = []

        def cb(h, _l):
            try:
                cls = win32gui.GetClassName(h)
                if "edit" in cls.lower() or "rich" in cls.lower():
                    found.append(h)
            except Exception:
                pass
            return True

        win32gui.EnumChildWindows(hwnd, cb, None)
        return found[0] if found else None
