"""输入注入引擎 V3：统一入口，通道从优到劣（UIA pattern -> PostMessage -> 前台守卫）"""
import ctypes
import logging
import threading
import time
from typing import Callable, Optional

from services.input.common import StepResult

log = logging.getLogger("input_engine")


def _timed_call(fn, timeout=2.5):
    """Run fragile UIA/COM calls with a hard wall-clock bound.

    Some Windows controls can block inside a COM provider indefinitely.  A
    daemon worker lets the input engine fall back to PostMessage/foreground
    injection and keeps Stop/Takeover responsive.
    """
    done = threading.Event()
    box = {}

    def run():
        com_inited = False
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
                com_inited = True
            except Exception:
                pass
            result = fn()
            box["result"] = result
        except Exception as exc:
            box["error"] = exc
        finally:
            if com_inited:
                try:
                    import pythoncom
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
            done.set()

    threading.Thread(target=run, name="tagent-uia-call", daemon=True).start()
    if not done.wait(timeout):
        log.warning("UIA call timed out after %.1fs; using fallback", timeout)
        return None
    if "error" in box:
        return None
    return box.get("result")
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
        if _timed_call(lambda: self.uia.invoke_at(x, y)):
            return StepResult(True, "uia_invoke")
        if self.msg.click(hwnd, x, y):
            return StepResult(True, "post_message_click")
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
        if self.msg.right_click(hwnd, x, y):
            return StepResult(True, "post_message_right_click")
        return self.fg.right_click(hwnd, x, y)

    def type_text(self, hwnd: int, text: str, keystroke_only: bool = False) -> StepResult:
        """keystroke_only=True 时跳过 SetValue/PostMessage 通道，直接键盘注入。
        拼音文本必须走真实键入：ValuePattern/PostMessage 直接设置文本，
        不经过输入法，'tielu ' 永远是字面量而得不到 '铁路'。"""
        replace = self._replace_next
        self._replace_next = False
        if not keystroke_only:
            if self.msg.replace_sel(hwnd, text):
                log.info("  type_text -> post_message_replace_sel (%d chars)", len(text))
                return StepResult(True, "post_message_replace_sel")
            if _timed_call(lambda: self.uia.set_text(hwnd, text, replace=replace)):
                log.info("  type_text -> uia_value_pattern (%d chars)", len(text))
                return StepResult(True, "uia_value_pattern")
        ok, reason = self.guard.ensure(hwnd)  # type-text fallback
        if not ok:
            return StepResult(False, "type_text", reason)
        if keystroke_only:
            # 前台切换刚完成时焦点尚未稳定，键会散落到旧焦点窗口/
            # IME 组合串收不到提交键；等一拍再注入
            time.sleep(0.3)
            log.info("  type_text -> foreground_type (IME 键入)")
            return self.fg.type_text(hwnd, text)
        if _timed_call(lambda: self.uia.send_keys(hwnd, text)):
            log.info("  type_text -> uia_send_keys (%d chars)", len(text))
            return StepResult(True, "uia_send_keys")
        log.warning("  type_text -> foreground_type (兜底)")
        return self.fg.type_text(hwnd, text)

    def invoke_ctrl(self, ctrl) -> bool:
        return bool(_timed_call(lambda: self.uia.invoke_ctrl(ctrl)))

    def key_press(self, hwnd: int, combo: str) -> StepResult:
        key = combo.strip().lower()
        if key in ("ctrl a", "ctrl+a", "ctrl  a"):
            if self.msg.select_all(hwnd):
                self._replace_next = True
                log.info("  key_press ctrl+a -> post_message_select_all")
                return StepResult(True, "post_message_select_all")
            if _timed_call(lambda: self.uia.select_all(hwnd)):
                self._replace_next = True
                log.info("  key_press ctrl+a -> uia_select_all")
                return StepResult(True, "uia_select_all")
            # UIA 全选失败也按「替换语义」处理：录制时 ctrl+A 后输入=替换
            self._replace_next = True
            log.info("  key_press ctrl+a -> uia_select_all 失败，仍按替换语义")
        vks = self._parse_combo(key)
        if not vks:
            return StepResult(False, "key_press", f"unknown_key: {combo}")
        # 修饰键组合（ctrl/alt/win+key）是命令意图：目标应用普遍不吃
        # PostMessage 的 WM_KEYDOWN 快捷键（如记事本 Ctrl+S 假成功不弹
        # 另存为），必须走真实键盘注入；仅单键保留 PostMessage 后台通道
        if len(vks) == 1 and self.msg.key_press(hwnd, vks[0]):
            log.info("  key_press %r -> post_message", key)
            return StepResult(True, "post_message_key")
        ok, reason = self.guard.ensure(hwnd)
        if not ok:
            return StepResult(False, "key_press", reason)
        if key in ("enter", "return"):
            if _timed_call(lambda: self.uia.enter(hwnd)):
                log.info("  key_press enter -> uia_enter")
                return StepResult(True, "uia_enter")
        vks = self._parse_combo(key)
        if not vks:
            return StepResult(False, "key_press", f"unknown_key: {combo}")
        log.info("  key_press %r -> foreground (%d vk)", key, len(vks))
        return self.fg.key_combo(hwnd, vks)

    _IME_TOGGLE_COMBOS = {"ctrl space", "ctrl+space", "ctrl  space",
                          "shift space", "shift+space"}

    @staticmethod
    def ime_mode_english():
        """读任务栏输入指示器判断当前中/英模式。
        返回 True=英语模式 / False=中文模式 / None=无法判定。"""
        try:
            import win32gui
            import uiautomation as ua
            h = win32gui.FindWindow("Shell_TrayWnd", None)
            if not h:
                return None
            tray = ua.ControlFromHandle(h)
            if tray is None:
                return None
            queue, depth_guard = [tray], 0
            while queue and depth_guard < 600:
                c = queue.pop(0)
                depth_guard += 1
                try:
                    n = str(c.Name or "")
                    if "输入指示" in n or "输入法" in n:
                        if "英语" in n:
                            return True
                        if "中文" in n or "拼" in n:
                            return False
                except Exception:
                    continue
                queue.extend(c.GetChildren())
        except Exception:
            pass
        return None

    def ime_toggle(self, hwnd: int, to_english: bool) -> StepResult:
        """把输入法切到确定的中/英模式。
        ctrl+space 是翻转语义：回放起点的输入法状态不可控（TSF 状态每应用
        粘滞），盲发一次可能得到与录制相反的模式，后续拼音串会变成字面字母。
        先读任务栏指示器，仅在当前模式与目标不符时才发送切换。"""
        english = self.ime_mode_english()
        if english is not None and english == to_english:
            log.info("  IME 已处于%s模式，跳过切换", "英" if english else "中")
            return StepResult(True, "ime_already_" + ("english" if english else "chinese"))
        ok, reason = self.guard.ensure(hwnd)
        if not ok:
            return StepResult(False, "ime_toggle", reason)
        # 用 fg.key_combo（AttachThreadInput 定向注入）而不是裸 keybd_event：
        # 裸全局键在目标窗口激活未稳时会切到桌面/其它应用
        result = self.fg.key_combo(hwnd, [0x11, 0x20])  # ctrl+space
        if not result.ok:
            return result
        time.sleep(0.3)
        english = self.ime_mode_english()
        if english is not None and english != to_english:
            return StepResult(False, "ime_toggle", "切换后模式不符")
        return StepResult(True, "ime_toggled")

    def _ime_force_closed(self, hwnd: int):
        """把目标窗口线程的输入法开合状态强制为关闭。
        ImmGetContext 只在拥有窗口的线程上有效，需先 AttachThreadInput。"""
        if not hwnd:
            return
        try:
            import ctypes
            import win32process
            target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            u32 = ctypes.windll.user32
            attached = target_tid != cur_tid
            if attached:
                u32.AttachThreadInput(cur_tid, target_tid, True)
            try:
                imm32 = ctypes.windll.imm32
                himc = imm32.ImmGetContext(hwnd)
                if himc:
                    imm32.ImmSetOpenStatus(himc, 0)
                    imm32.ImmReleaseContext(hwnd, himc)
                    log.info("  key_press IME 切换前强制关闭输入法（确定性语义）")
            finally:
                if attached:
                    try:
                        u32.AttachThreadInput(cur_tid, target_tid, False)
                    except Exception:
                        pass
        except Exception as e:
            log.debug("IME 状态同步失败: %s", e)

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
