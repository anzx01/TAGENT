"""PostMessage 后台通道：不依赖前台、不抢鼠标键盘（ScreenClaw 路线）"""
import ctypes
import logging
import time
from typing import Optional

import win32gui

from services.input.common import EM_REPLACESEL, MK_LBUTTON, MK_RBUTTON, \
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP

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
            ctypes.windll.user32.SendMessageW(edit, EM_REPLACESEL, 1, buf)
            return True
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
