"""输入注入引擎 V3：统一入口，通道从优到劣（UIA pattern -> PostMessage -> 前台守卫）"""
import logging
from typing import Callable, Optional

from services.input.common import StepResult

log = logging.getLogger("input_engine")
from services.input.foreground_channel import ForegroundChannel
from services.input.post_message_channel import PostMessageChannel
from services.input.uia_channel import UiaChannel


class InputEngine:
    def __init__(self):
        self.uia = UiaChannel()
        self.msg = PostMessageChannel()
        self.fg = ForegroundChannel()
        self.guard = self.fg.guard
        self._replace_next = False

    def click(self, hwnd: int, x: int, y: int) -> StepResult:
        if self.uia.invoke_at(x, y):
            return StepResult(True, "uia_invoke")
        self.msg.click(hwnd, x, y)
        return self.fg.click(hwnd, x, y)

    def click_desktop(self, x: int, y: int) -> StepResult:
        self.fg._mouse_click(x, y)
        return StepResult(True, "desktop_click")

    def double_click(self, hwnd: int, x: int, y: int) -> StepResult:
        self.fg._mouse_double_click(x, y)
        return StepResult(True, "double_click")

    def double_click_desktop(self, x: int, y: int) -> StepResult:
        self.fg._mouse_double_click(x, y)
        return StepResult(True, "desktop_double_click")

    def right_click(self, hwnd: int, x: int, y: int) -> StepResult:
        return self.fg.right_click(hwnd, x, y)

    def type_text(self, hwnd: int, text: str) -> StepResult:
        replace = self._replace_next
        self._replace_next = False
        if self.uia.set_text(hwnd, text, replace=replace):
            log.info("  type_text -> uia_value_pattern (%d chars)", len(text))
            return StepResult(True, "uia_value_pattern")
        log.warning("  type_text: uia_value_pattern 不可用，尝试备用通道")
        ok, reason = self.guard.ensure(hwnd)
        if not ok:
            return StepResult(False, "type_text", reason)
        if self.uia.send_keys(hwnd, text):
            log.info("  type_text -> uia_send_keys (%d chars)", len(text))
            return StepResult(True, "uia_send_keys")
        if self.msg.replace_sel(hwnd, text):
            log.info("  type_text -> post_message_replace_sel (%d chars)", len(text))
            return StepResult(True, "post_message_replace_sel")
        log.warning("  type_text -> foreground_type (兜底)")
        return self.fg.type_text(hwnd, text)

    def key_press(self, hwnd: int, combo: str) -> StepResult:
        key = combo.strip().lower()
        if key in ("ctrl a", "ctrl+a", "ctrl  a"):
            if self.uia.select_all(hwnd):
                self._replace_next = True
                log.info("  key_press ctrl+a -> uia_select_all")
                return StepResult(True, "uia_select_all")
            # UIA 全选失败也按「替换语义」处理：录制时 ctrl+A 后输入=替换
            self._replace_next = True
            log.info("  key_press ctrl+a -> uia_select_all 失败，仍按替换语义")
        ok, reason = self.guard.ensure(hwnd)
        if not ok:
            return StepResult(False, "key_press", reason)
        if key in ("enter", "return"):
            if self.uia.enter(hwnd):
                log.info("  key_press enter -> uia_enter")
                return StepResult(True, "uia_enter")
        vks = self._parse_combo(key)
        if not vks:
            return StepResult(False, "key_press", f"unknown_key: {combo}")
        log.info("  key_press %r -> foreground (%d vk)", key, len(vks))
        return self.fg.key_combo(hwnd, vks)

    def scroll(self, x: int, y: int, delta: int) -> None:
        self.fg.scroll(x, y, delta)

    def drag(self, sx: int, sy: int, ex: int, ey: int,
             stopped: Optional[Callable[[], bool]] = None) -> None:
        self.fg.drag(sx, sy, ex, ey, stopped)

    def _parse_combo(self, key: str) -> list:
        from services.input.common import VK_MAP
        vks: list = []
        for part in key.split():
            if part in VK_MAP:
                vks.append(VK_MAP[part])
            elif len(part) == 1:
                vks.append(ord(part.upper()))
        return vks
