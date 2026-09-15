"""UIA pattern 通道：优先用 pattern 驱动，不注入输入（winappCli 推荐）"""
import ctypes
import logging
import time
from typing import Optional

import win32gui
import uiautomation as ua

log = logging.getLogger("input_engine")


class UiaChannel:
    def __init__(self):
        self._edit_cache = {}  # hwnd -> (runtime_id, control)

    def _root(self, hwnd: Optional[int]):
        # UIA 在非主线程使用前必须初始化 COM（重复调用返回 S_FALSE，无害）
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        if hwnd and win32gui.IsWindow(hwnd):
            return ua.ControlFromHandle(hwnd)
        return ua.GetFocusedControl()

    def _find_edit(self, hwnd: int, depth: int = 12):
        root = self._root(hwnd)
        if not root:
            return None
        key = hwnd
        try:
            import win32process
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        except Exception:
            pid = None
        cached = self._edit_cache.get(key)
        if cached:
            cpid, rid, ctrl = cached
            try:
                if pid == cpid and ctrl.GetRuntimeId() == rid and ctrl.IsWindow():
                    return ctrl
            except Exception:
                pass
        for cand in ("文件名:", "文件名(&N):", "文件名(&N)", "文件名(*)"):
            try:
                ec = root.EditControl(searchDepth=depth, Name=cand)
                if ec and ec.Exists(0.3):
                    try:
                        rid = ec.GetRuntimeId()
                    except Exception:
                        rid = None
                    self._edit_cache[key] = (pid, rid, ec)
                    return ec
            except Exception:
                continue
        try:
            f = ua.GetFocusedControl()
            if f and f.ControlTypeName == "EditControl":
                try:
                    rid = f.GetRuntimeId()
                except Exception:
                    rid = None
                self._edit_cache[key] = (pid, rid, f)
                return f
        except Exception:
            pass
        for ct in ("DocumentControl", "EditControl", "TextControl"):
            ec = getattr(root, ct)(searchDepth=depth)
            if ec.Exists(0.3):
                try:
                    rid = ec.GetRuntimeId()
                except Exception:
                    rid = None
                self._edit_cache[key] = (pid, rid, ec)
                return ec
        return None

    def set_text(self, hwnd: int, text: str, replace: bool = False) -> bool:
        """ValuePattern 写入。replace=False：追加语义；replace=True：整体替换。

        追加时自校验：写入后读回不符说明存在选中文本被覆盖，改按替换重写。
        """
        try:
            edit = self._find_edit(hwnd)
            if not edit:
                return False
            vp = edit.GetValuePattern()
            if not vp:
                return False
            if replace:
                vp.SetValue(text)
                return True
            try:
                existing = vp.Value or ""
            except Exception:
                existing = ""
            expected = existing + text
            vp.SetValue(expected)
            time.sleep(0.15)
            try:
                actual = vp.Value or ""
            except Exception:
                actual = None
            if actual is not None and actual != expected:
                vp.SetValue(text)
                time.sleep(0.15)
            return True
        except Exception as e:
            log.debug("UIA set_text 失败: %s", e)
            return False

    def select_all(self, hwnd: int) -> bool:
        """DocumentRange.Select 全选（UWP 记事本等 keybd 注入失效时的替代）"""
        try:
            edit = self._find_edit(hwnd)
            if not edit:
                return False
            edit.GetTextPattern().DocumentRange.Select()
            return True
        except Exception as e:
            log.debug("UIA select_all 失败: %s", e)
            return False

    def send_keys(self, hwnd: int, text: str) -> bool:
        """焦点控件 SendKeys 逐字符（ValuePattern 不可用时的 UIA 回退）"""
        try:
            edit = self._find_edit(hwnd)
            if not edit:
                return False
            edit.SetFocus()
            time.sleep(0.1)
            u32 = ctypes.windll.user32
            u32.keybd_event(0x11, 0, 0, 0)
            u32.keybd_event(0x23, 0, 0, 0)
            u32.keybd_event(0x23, 0, 2, 0)
            u32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.05)
            for char in text:
                if char == "\n":
                    edit.SendKeys("{Enter}")
                elif char == "\t":
                    edit.SendKeys("{Tab}")
                elif char == " ":
                    u32.keybd_event(0x20, u32.MapVirtualKeyW(0x20, 0), 0, 0)
                    u32.keybd_event(0x20, 0, 2, 0)
                    time.sleep(0.02)
                elif char in "{}[]()^%~+":
                    edit.SendKeys("{%s}" % char)
                else:
                    edit.SendKeys(char, interval=0.02)
            return True
        except Exception as e:
            log.debug("UIA send_keys 失败: %s", e)
            return False

    def invoke_at(self, x: int, y: int) -> bool:
        """点击位置元素优先走 InvokePattern（winappCli: UIA pattern 优先）"""
        try:
            element = ua.ElementFromPoint(x, y)
            if not element:
                return False
            return self.invoke_ctrl(element)
        except Exception:
            return False

    def invoke_ctrl(self, ctrl) -> bool:
        """对已定位控件做 pattern 操作：Invoke > Select > Expand，任一成功返回 True。
        不模拟鼠标——控件语义操作（winappCli 推荐）。
        用 GetPattern(PatternId) 通用获取，兼容 Control 基类实例（无子类 pattern 方法）。"""
        import uiautomation as ua
        for pattern_id, method in ((ua.PatternId.InvokePattern, "Invoke"),
                                   (ua.PatternId.SelectionItemPattern, "Select"),
                                   (ua.PatternId.ExpandCollapsePattern, "Expand")):
            try:
                pat = ctrl.GetPattern(pattern_id)
                if pat is None:
                    continue
                getattr(pat, method)()
                return True
            except Exception as e:
                log.debug("UIA %s pattern 失败: %r", method, e)
        return False

    def _find_save_dialog(self):
        """找屏幕上的保存类对话框（另存为/保存文件等），返回其文件名框 Ctrl"""
        import win32gui
        out = []

        def cb(h, l):
            t = win32gui.GetWindowText(h)
            if win32gui.IsWindowVisible(h) and t and (
                    "另存为" in t or "保存" in t):
                l.append(h)
            return True
        win32gui.EnumWindows(cb, out)
        for h in out:
            try:
                root = ua.ControlFromHandle(h)
                for cand in ("文件名:", "文件名(&N):", "文件名(&N)", "文件名(*)"):
                    try:
                        ec = root.EditControl(searchDepth=12, Name=cand)
                        if ec and ec.Exists(0.3):
                            return root
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    def enter(self, hwnd: Optional[int] = None) -> bool:
        """发送 Enter：优先注入到（任一）保存对话框的文件名框，其次焦点控件。
        不依赖键盘焦点是否已在文件名框（XAML 对话框被程序激活后焦点可能停留在别处）。"""
        try:
            if hwnd:
                edit = self._find_edit(hwnd)
                if edit:
                    try:
                        edit.SendKeys("{Enter}")
                        log.info("  uia_enter: 对目标窗口编辑框 %r 注入 Enter", edit.Name)
                        return True
                    except Exception as e:
                        log.debug("uia_enter edit.SendKeys 失败: %s", e)
            root = self._find_save_dialog()
            if root is not None:
                for cand in ("文件名:", "文件名(&N):", "文件名(&N)", "文件名(*)"):
                    try:
                        ec = root.EditControl(searchDepth=12, Name=cand)
                        if ec and ec.Exists(0.3):
                            ec.SendKeys("{Enter}")
                            log.info("  uia_enter: 对保存对话框编辑框 %r 注入 Enter", ec.Name)
                            return True
                    except Exception:
                        continue
            focused = ua.GetFocusedControl()
            if not focused:
                log.info("  uia_enter: 无焦点控件")
                return False
            log.info("  uia_enter: 焦点=%s name=%r", getattr(focused, "ControlTypeName", "?"),
                     getattr(focused, "Name", ""))
            try:
                focused.GetInvokePattern().Invoke()
                log.info("  uia_enter: Invoke 成功 (%r)", getattr(focused, "Name", ""))
                return True
            except Exception:
                pass
            try:
                focused.SendKeys("{Enter}")
                return True
            except Exception:
                pass
        except Exception:
            pass
        return False
