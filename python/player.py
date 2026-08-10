"""
回放引擎 V2 - 支持按 UI Automation 证据重新定位元素
定位优先级：UIA 锚点 > 坐标百分比 > 原始屏幕坐标
"""
import ctypes
import json
import time
from pathlib import Path

import win32gui
import win32process
from logger import get_logger

log = get_logger("player")

EDIT_CLASSES = ("Edit", "RichEditD2DPT", "RichEdit20W", "RICHEDIT50W")
RELEASE_MODIFIERS = [0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C]
MAX_TEXT_CHAR_INTERVAL = 0.03

VK_MAP = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
}
for i in range(12):
    VK_MAP[f"f{i+1}"] = 0x70 + i


class Player:
    def __init__(self, events_path, variables=None, session_steps=None,
                 corrections=None, var_sources=None):
        self.events_path = Path(events_path)
        with open(self.events_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 支持 events.json 和 task.json
        self.steps = data.get("steps", [])
        self._stopped = False
        self.variables = variables or {}
        self.var_sources = var_sources or {}  # 阶段2.1：变量来源绑定
        self.on_pause = None  # 阶段3：等待人工接管时的回调（state 更新）
        self._paused = False
        self._resume = None
        self._session_steps = set(session_steps or [])  # 阶段2.4
        self.checkpoint_path = None  # 阶段5.1：断点续跑
        self._corrections = corrections or []  # 阶段3.3：自愈库
        self._step_results = []  # 阶段5.2：逐步结果
        self._pause_reasons = []  # 阶段5.2：暂停原因
        self.screenshot_dir = None  # 阶段1.2：回放截图目录
        self._rec_id = ""  # 录制 ID（用于重新演示此步）

    def stop(self):
        self._stopped = True
        self._resume_cond_resume()

    def pause(self):
        """请求暂停（阶段3.1：定位/验证失败时）"""
        self._paused = True

    def resume(self):
        """人工接管完成后继续回放"""
        self._paused = False
        self._resume_cond_resume()
        if self._resume:
            self._resume.set()

    def _resume_cond_resume(self):
        if self._resume:
            self._resume.set()
            self._resume = None

    @property
    def paused(self):
        return self._paused

    def play(self, step_delay=0.5, start_delay=0.5, max_wait=15.0, from_step=0):
        """逐步回放。单步最长等待 max_wait 秒，防止录制长停顿导致回放卡死。
        from_step: 阶段5.1 断点续跑，从指定步骤继续。
        返回 dict: {summary, steps, pause_reasons}。"""
        self._stopped = False
        self._paused = False
        self._step_results = []
        self._pause_reasons = []
        if start_delay > 0:
            time.sleep(start_delay)
        # 强制前台窗口获得焦点
        try:
            fg = win32gui.GetForegroundWindow()
            if fg:
                _force_foreground(fg)
        except:
            pass
        _check_foreground()
        # 释放所有修饰键（测试脚本的 Ctrl+A 等操作可能导致 VK_LCONTROL 卡住）
        _release_keys(RELEASE_MODIFIERS)
        time.sleep(0.2)
        prev_t = 0.0
        done = from_step
        for i, step in enumerate(self.steps[from_step:], start=from_step):
            if self._stopped:
                break
            if step.get("action") == "wait":
                self._do_wait(step, prev_t)
                prev_t = step.get("t", 0)
                continue
            wait = max(0.0, step.get("t", 0) - prev_t)
            time.sleep(min(wait, max_wait))
            if self._stopped:
                break
            # 阶段2.4：会话步骤（登录/2FA）暂停，等人工
            if i in self._session_steps:
                self._pause_reasons.append({"step": i, "reason": "会话步骤(登录/2FA)"})
                self._pause_for_human(i)
            if self._stopped:
                break
            action = step.get("action", "?")
            text = step.get("text", step.get("key", ""))
            log.info("[回放 %d/%d] %s %s", i + 1, len(self.steps), action, text)
            step_ok = True
            step_detail = ""
            # 阶段4 安全效果标签：破坏性操作暂停确认
            if step.get("effect") == "destructive":
                self._pause_reasons.append({"step": i, "reason": f"破坏性操作需确认: {action} {text[:20]}"})
                log.warning("  步骤 %d 破坏性操作，暂停确认", i)
                self._pause_for_human(i)
                if self._stopped:
                    break
            # 阶段3.1：定位失败 -> 尝试纠错库 -> 尝试 LLM -> 暂停
            locator_failed = False
            if step.get("action") in ("click", "right_click") and step.get("locator"):
                if not self._check_locator(step):
                    locator_failed = True
                    if self._try_correction(i):
                        step_detail = "纠错库命中"
                    else:
                        llm_step = self._try_llm_heal(i, step, "定位失败")
                        if llm_step:
                            self._play_step(llm_step)
                            step_detail = "LLM自愈成功"
                        else:
                            step_ok = False
                            step_detail = "定位失败"
                            self._pause_reasons.append({"step": i, "reason": f"定位失败: {action}"})
                            log.warning("  步骤 %d 定位失败，暂停等待人工", i)
                            self._pause_for_human(i)
                            if self._stopped:
                                break
            if step_ok and not locator_failed:
                if not self._try_correction(i):
                    self._play_step(step)
                step_detail = step_detail or "成功"
            # 截图验证
            self._capture_step_screenshot(i)
            self._step_results.append({
                "step": i, "action": action, "text": text[:50],
                "status": "ok" if step_ok else "paused", "detail": step_detail,
                "ts": time.strftime("%H:%M:%S")
            })
            done = i + 1
            self._save_checkpoint(i + 1)
            prev_t = step.get("t", 0)
            time.sleep(step_delay)
        interrupted = self._stopped
        summary = f"完成 {done}/{len(self.steps)} 步" + (" (已中断)" if interrupted else "")
        return {"summary": summary, "steps": self._step_results,
                "pause_reasons": self._pause_reasons}

    def _pause_for_human(self, step_index):
        """阶段2.4：会话步骤（登录/2FA）暂停，等人工完成"""
        self._paused = True
        log.info("会话步骤 %d：等待人工登录/验证", step_index)
        while self._paused and not self._stopped:
            time.sleep(0.3)
        self._paused = False

    def _save_checkpoint(self, next_idx):
        """阶段5.1：每步完成写 checkpoint，崩溃后可续跑"""
        if not self.checkpoint_path:
            return
        try:
            self.checkpoint_path.write_text(json.dumps(
                {"next_step": next_idx, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}),
                encoding="utf-8")
        except Exception:
            pass

    def _check_locator(self, step):
        """阶段3.1：检查定位策略链是否能找到元素，失败返回 False"""
        locators = step.get("locator", [])
        if not locators:
            return True  # 无 locator 不检查
        raw = (step.get("screen_x"), step.get("screen_y"))
        for loc in locators:
            x, y = self._try_locator(loc, raw)
            if x is not None and y is not None:
                return True
        return False

    def _capture_step_screenshot(self, step_idx):
        """阶段1.2：回放每步后截图验证"""
        if not self.screenshot_dir:
            return
        try:
            fg = win32gui.GetForegroundWindow()
            if not fg:
                return
            from capture import capture_window
            img = capture_window(fg)
            if img:
                path = Path(self.screenshot_dir) / f"step_{step_idx:03d}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                img.save(str(path))
        except Exception as e:
            log.debug("截图失败(step %d): %s", step_idx, e)

    def _try_correction(self, idx):
        """阶段3.3 自愈：当前步骤与纠错库 trigger 匹配时，改用人工纠正后的步骤执行。
        执行后调用 record_trial 回报成功/失败，形成信任闭环。"""
        if not self._corrections or idx == 0:
            return False
        from services.corrections import CorrectionStore as _CS
        context = [self.steps[idx - 1], self.steps[idx]]
        sig = "|".join(_CS._step_signal(s) for s in context)
        best = None
        best_score = 0
        for corr in self._corrections:
            trig = corr.get("trigger", [])
            if not trig:
                continue
            t_sig = "|".join(_CS._step_signal(s) for s in trig[-2:])
            overlap = sum(1 for p in sig.split("|") if p and p in t_sig)
            if overlap > best_score:
                best_score = overlap
                best = corr
        if best is None or best_score == 0:
            return False
        correct = best.get("correct", [])
        if not correct:
            return False
        corr_id = best.get("id", "")
        log.info("  命中纠错 %s：回放人工纠正步骤 (%d 步)", corr_id, len(correct))
        success = True
        for c in correct:
            if self._stopped:
                success = False
                break
            self._play_step(c)
            time.sleep(0.4)
        # 阶段3.3 信任闭环：回报试用结果，成功+1，失败清零
        if corr_id:
            try:
                from services.corrections import CorrectionStore
                store = CorrectionStore(self.events_path.parent)
                store.record_trial(corr_id, success)
                frozen = best.get("frozen", 0) + (1 if success else 0)
                log.info("  纠错 %s 信任计数 -> %d", corr_id, frozen)
            except Exception as e:
                log.debug("信任回报失败: %s", e)
        return True

    def _try_llm_heal(self, idx, failed_step, error=""):
        """阶段3.3 LLM 自愈：纠错库未命中时，向 LLM 请求纠正建议"""
        try:
            from services.llm_heal import get_healer
            healer = get_healer()
            if not healer.enabled:
                return None
            context = self.steps[max(0, idx-2):idx]
            log.info("  尝试 LLM 自愈 (步骤 %d)...", idx)
            result = healer.heal(failed_step, context, error)
            if result and isinstance(result, dict):
                log.info("  LLM 自愈建议: %s", result.get("action", "?"))
                return result
        except Exception as e:
            log.debug("LLM 自愈异常: %s", e)
        return None

    def _do_wait(self, step, prev_t):
        """等待步骤：等待目标窗口出现（阶段2.3），超时则继续"""
        duration = float(step.get("duration", 2.0))
        target = step.get("window", "")
        deadline = time.time() + duration
        while time.time() < deadline:
            if self._stopped:
                return
            if target and _window_exists(target):
                log.info("  等待结束：窗口已出现 %r", target)
                return
            time.sleep(0.3)
        if target:
            log.info("  等待超时：窗口未出现 %r", target)

    def _play_step(self, step):
        action = step.get("action")
        if action == "click":
            self._do_click(step)
        elif action == "right_click":
            self._do_right_click(step)
        elif action == "input_text":
            self._do_input_text(step)
        elif action == "key_press":
            self._do_key_press(step)
        elif action == "scroll":
            self._do_scroll(step)
        elif action in ("swipe", "drag"):
            self._do_drag(step)
        elif action == "switch_window":
            self._do_switch_window(step)

    # ============ 定位 ============

    def _resolve_coords(self, step):
        """用定位策略链找到目标屏幕坐标，返回 (x, y)"""
        locators = step.get("locator", [])
        raw = (step.get("screen_x"), step.get("screen_y"))
        # 如果没有 locator，用原始坐标
        if not locators:
            return raw

        for loc in locators:
            x, y = self._try_locator(loc, raw)
            if x is not None and y is not None:
                # UIA 控件中心与原始点击点偏离过大时，信任原始坐标
                rx, ry = raw
                if loc.get("type") == "uia" and rx is not None and ry is not None:
                    if abs(x - rx) > 150 or abs(y - ry) > 150:
                        log.info("  [定位] UIA 结果 (%s,%s) 偏离原始点 (%s,%s)，回退原始坐标", x, y, rx, ry)
                        return rx, ry
                return x, y

        # 全部失败，用原始坐标
        return raw

    def _try_locator(self, loc, raw=(None, None)):
        loc_type = loc.get("type")
        if loc_type == "uia":
            return self._locate_by_uia(loc, raw)
        elif loc_type == "coord":
            return self._locate_by_coord(loc)
        elif loc_type == "screen_coord":
            return loc.get("x"), loc.get("y")
        elif loc_type == "dom":
            return self._locate_by_dom(loc)
        elif loc_type == "ocr":
            return self._locate_by_ocr(loc)
        return None, None

    def _locate_by_dom(self, loc):
        """阶段2.2：Playwright DOM 定位（浏览器元素）"""
        selector = loc.get("selector", "")
        if not selector:
            return None, None
        try:
            import asyncio
            from services.dom_locator import get_browser_debug_port, locate_by_dom, connect_browser

            port = get_browser_debug_port(win32gui.GetForegroundWindow())
            if not port:
                return None, None

            async def _do():
                browser, pw = await connect_browser(port)
                if not browser:
                    return None
                try:
                    contexts = browser.contexts
                    if not contexts:
                        return None
                    pages = contexts[0].pages
                    if not pages:
                        return None
                    # 使用第一个页面
                    page = pages[0]
                    return await locate_by_dom(page, selector)
                finally:
                    await pw.stop()

            result = asyncio.new_event_loop().run_until_complete(_do())
            return result or (None, None)
        except Exception as e:
            log.debug("DOM 定位失败: %s", e)
            return None, None

    def _locate_by_ocr(self, loc):
        """阶段1.1：OCR 文本兜底定位"""
        target_text = loc.get("text", "")
        if not target_text:
            return None, None
        try:
            from services.ocr_locator import capture_and_find
            hwnd = win32gui.GetForegroundWindow()
            result = capture_and_find(target_text, hwnd=hwnd)
            return result or (None, None)
        except Exception as e:
            log.debug("OCR 定位失败: %s", e)
            return None, None

    def _find_top_window(self, ua, loc):
        """按父窗口链定位顶级窗口，失败回退前台窗口"""
        for p in reversed(loc.get("parents", [])):
            p_name = p.get("name", "")
            p_class = p.get("class", "")
            hwnd = None
            if p_name:
                hwnd = win32gui.FindWindow(None, p_name)
            if hwnd is None and p_class:
                hwnd = win32gui.FindWindow(p_class, None)
            if hwnd:
                return ua.ControlFromHandle(hwnd)
        fg = win32gui.GetForegroundWindow()
        return ua.ControlFromHandle(fg)

    def _locate_by_uia(self, loc, raw=(None, None)):
        """用 UI Automation 重新定位元素。多属性组合搜索，避免逐类型穷举。"""
        try:
            import uiautomation as ua
            target_type = loc.get("control_type", "")
            target_name = loc.get("name", "")
            target_auto_id = loc.get("auto_id", "")
            target_class = loc.get("class", "")

            ctrl_type = None
            if target_type:
                ctrl_type = getattr(ua.ControlType, target_type, None)
                if not isinstance(ctrl_type, int):
                    ctrl_type = _control_type_by_name(target_type)

            # 组合搜索（AutomationId 最可靠，优先单独查；其余组合查）
            candidates = []
            if target_auto_id:
                candidates.append((dict(AutomationId=target_auto_id), True))
            kw = {}
            if ctrl_type is not None:
                kw["ControlType"] = ctrl_type
            if target_name and target_auto_id:
                kw["Name"] = target_name
            if kw:
                candidates.append((kw, False))
            if target_class and target_auto_id:
                candidates.append((dict(ClassName=target_class), False))

            window = self._find_top_window(ua, loc)
            if window is None:
                return None, None

            def _point(ctrl):
                rect = ctrl.BoundingRectangle
                # 原始点击点落在控件内时优先使用原始坐标（大容器中心会偏离点击点）
                rx, ry = raw
                if rx is not None and ry is not None:
                    if rect.left <= rx <= rect.right and rect.top <= ry <= rect.bottom:
                        return rx, ry
                return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2

            for kw, _ in candidates:
                ctrl = window.Control(**kw, searchDepth=32)
                if ctrl.Exists(0.5):
                    return _point(ctrl)

            # 兄弟索引兜底（父控件下按索引取）
            if "sibling_index" in loc and loc.get("parents"):
                p = loc["parents"][0]
                p_class = p.get("class", "")
                if p_class:
                    hwnd = win32gui.FindWindow(p_class, None)
                    if hwnd:
                        parent_ctrl = ua.ControlFromHandle(hwnd)
                        if parent_ctrl:
                            children = parent_ctrl.GetChildren()
                            idx = loc["sibling_index"]
                            if idx < len(children):
                                return _point(children[idx])
        except Exception as e:
            log.info("[UIA 定位失败] %s", e)
        return None, None

    def _locate_by_coord(self, loc):
        """坐标定位：优先窗口内百分比，其次屏幕百分比"""
        # 窗口内相对坐标（先定位窗口再换算屏幕坐标）
        client_pct_x = loc.get("client_pct_x")
        client_pct_y = loc.get("client_pct_y")
        window_title = loc.get("window", "")
        if client_pct_x is not None and client_pct_y is not None and window_title:
            hwnd = win32gui.FindWindow(None, window_title)
            if hwnd:
                try:
                    rect = win32gui.GetWindowRect(hwnd)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    if w > 0 and h > 0:
                        return rect[0] + int(w * client_pct_x / 100), rect[1] + int(h * client_pct_y / 100)
                except Exception:
                    pass

        # 2. 屏幕百分比坐标
        pct_x = loc.get("pct_x")
        pct_y = loc.get("pct_y")
        if pct_x is None or pct_y is None:
            return None, None
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        return int(sw * pct_x / 100), int(sh * pct_y / 100)
    def _do_switch_window(self, step):
        """切换窗口：win32 _force_foreground 优先（快），pywinauto 兜底（慢但灵活）。
        等待目标窗口出现（最多 3 秒），避免窗口尚未打开就尝试切换。"""
        title = step.get("window", "")
        hwnd = step.get("hwnd")
        if not title and not hwnd:
            return

        # 策略1: win32 _find_window_by_title + _force_foreground（快，最可靠）
        h = None
        if title:
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if self._stopped:
                    return
                h = _find_window_by_title(title)
                if h:
                    break
                time.sleep(0.3)
        else:
            h = hwnd

        if h and win32gui.IsWindow(h):
            if _force_foreground(h):
                log.info("  已切换窗口: %r (hwnd=%s)", title, h)
                time.sleep(0.3)
                return
            # _force_foreground 失败，尝试简单的 SetForegroundWindow
            try:
                win32gui.ShowWindow(h, 9)  # SW_RESTORE
                win32gui.SetForegroundWindow(h)
                log.info("  已切换窗口(SFW): %r (hwnd=%s)", title, h)
                time.sleep(0.3)
                return
            except Exception:
                pass

        # 策略2: pywinauto 兜底（对 win32 找不到的窗口类型）
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            dlg = None
            if title:
                # 先精确匹配，再模糊匹配
                try:
                    dlg = desktop.window(title=title)
                    dlg.wait('exists', timeout=1)
                except Exception:
                    dlg = None
                if not dlg:
                    # 用 contains 匹配（比 title_re 更宽松）
                    try:
                        dlg = desktop.window(title_re=".*" + title.replace("[", "\\[").replace("]", "\\]") + ".*")
                        dlg.wait('exists', timeout=1)
                    except Exception:
                        dlg = None
            else:
                dlg = desktop.window(handle=hwnd)

            if dlg:
                dlg.set_focus()
                log.info("  pywinauto 已切换窗口: %r", title)
                time.sleep(0.3)
                return
        except Exception:
            pass

        log.warning("  切换窗口失败：未找到 %r", title)

    def _render_text(self, text):
        """阶段2.1 数据泛化：替换 {{var}} 占位符为运行时的变量值。
        支持变量来源绑定（阶段2.1）：clipboard 从剪贴板取值，ui 从当前界面取值。"""
        if "{{" not in text:
            return text
        import re
        # 先解析未提供的变量（从来源提取）
        for m in re.finditer(r"\{\{\s*(\w+)\s*\}\}", text):
            var_name = m.group(1).strip()
            if var_name not in self.variables:
                val = self._extract_var(var_name)
                if val is not None:
                    self.variables[var_name] = val
        def _sub(m):
            return str(self.variables.get(m.group(1).strip(), m.group(0)))
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", _sub, text)

    def _extract_var(self, var_name):
        """阶段2.1：从来源提取变量值（剪贴板/UIA 控件）"""
        sources = self.var_sources or {}
        src = sources.get(var_name)
        if not src:
            return None
        src_type = src.get("type")
        if src_type == "clipboard":
            try:
                import win32clipboard
                win32clipboard.OpenClipboard()
                try:
                    data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    return data if data else None
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                return None
        elif src_type == "ui":
            try:
                import uiautomation as ua
                fg = win32gui.GetForegroundWindow()
                root = ua.ControlFromHandle(fg)
                if not root:
                    return None
                name = src.get("control_name", "")
                ctrl_type = src.get("control_type", "EditControl")
                ctrl = getattr(root, ctrl_type)(Name=name, searchDepth=32)
                if ctrl.Exists(2.0):
                    try:
                        return ctrl.GetValuePattern().Value
                    except Exception:
                        pass
                    try:
                        return ctrl.Name
                    except Exception:
                        pass
            except Exception:
                return None
        return None

    # ============ 动作执行 ============

    # 桌面级窗口标题/类名：这些窗口用前台物理点击（mouse_event），不能用 PostMessage
    _DESKTOP_WINDOWS = {"Program Manager", "主机弹出窗口", ""}

    def _is_desktop_action(self, step):
        """判断是否为桌面级操作（右键菜单、桌面点击等），需用前台物理点击"""
        window = step.get("window", "")
        return window in self._DESKTOP_WINDOWS or not window

    def _do_click(self, step):
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return
        window_title = step.get("window", "")
        if self._is_desktop_action(step):
            # 桌面/右键菜单：前台物理点击
            log.info("  -> 前台点击 (%d,%d)", x, y)
            _mouse_click(x, y)
        else:
            # 应用窗口：先切换到目标窗口，再用前台物理点击
            # PostMessage 后台点击对浏览器/IDE 等现代应用不生效，统一用物理点击
            if window_title:
                hwnd = _find_window_by_title(window_title)
                if hwnd and win32gui.IsWindow(hwnd):
                    fg = win32gui.GetForegroundWindow()
                    if fg != hwnd:
                        _force_foreground(hwnd)
                        time.sleep(0.2)
            log.info("  -> 前台点击 (%d,%d) -> %r", x, y, window_title)
            _mouse_click(x, y)

    def _do_right_click(self, step):
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return
        window_title = step.get("window", "")
        if self._is_desktop_action(step):
            # 桌面右键菜单：前台物理点击，右键后等待菜单出现
            log.info("  -> 前台右键 (%d,%d)", x, y)
            _mouse_right_click(x, y)
            time.sleep(0.5)  # 等待右键菜单弹出
        else:
            # 应用窗口：先切换到目标窗口，再用前台物理右键
            if window_title:
                hwnd = _find_window_by_title(window_title)
                if hwnd and win32gui.IsWindow(hwnd):
                    fg = win32gui.GetForegroundWindow()
                    if fg != hwnd:
                        _force_foreground(hwnd)
                        time.sleep(0.2)
            log.info("  -> 前台右键 (%d,%d) -> %r", x, y, window_title)
            _mouse_right_click(x, y)
            time.sleep(0.5)

    def _do_input_text(self, step):
        """文本输入：先切换窗口，再根据应用类型选择输入方式。
        - 记事本(RichEdit): pywinauto type_keys(with_spaces=True) -- 解决空格丢失
        - 其他应用(IDE/浏览器): SendInput -- 最快最可靠"""
        text = self._render_text(step.get("text", ""))
        if not text:
            return
        target_window = step.get("window", "")

        # 步骤1: 确保目标窗口在前台
        hwnd = None
        if target_window:
            hwnd = _find_window_by_title(target_window)
            if hwnd and win32gui.IsWindow(hwnd):
                fg = win32gui.GetForegroundWindow()
                if fg != hwnd:
                    _force_foreground(hwnd)
                    time.sleep(0.2)

        # 步骤2: 检测是否是记事本（Win11 RichEdit 丢空格，需要特殊处理）
        is_notepad = False
        if target_window and "notepad" in target_window.lower():
            is_notepad = True
        elif hwnd:
            try:
                cls = win32gui.GetClassName(hwnd)
                if cls == "Notepad":
                    is_notepad = True
            except:
                pass

        if is_notepad:
            # 记事本: 用 pywinauto type_keys(with_spaces=True)
            log.info("  [记事本] pywinauto type_keys 输入: %r", text[:50])
            if self._pywinauto_type_keys(target_window, text):
                return
            # pywinauto 失败，用 keybd_event 逐字符（比 SendInput 对空格更可靠）
            log.info("  [记事本] pywinauto 失败，用 keybd_event 逐字符")
            self._keybd_input_text(text)
            return

        # 其他应用: SendInput 直接输入
        log.info("  SendInput 输入: %r", text[:50])
        for char in text:
            if self._stopped:
                break
            _send_input_char(char)
            time.sleep(MAX_TEXT_CHAR_INTERVAL)
        log.info("  SendInput 输入完成")
        return

    def _keybd_input_text(self, text):
        """用 keybd_event 逐字符输入（对 RichEdit 空格更可靠）"""
        u32 = ctypes.windll.user32
        for char in text:
            if self._stopped:
                break
            if char == '\n':
                # Enter
                u32.keybd_event(0x0D, 0, 0, 0)
                u32.keybd_event(0x0D, 0, 2, 0)
            elif char == ' ':
                # 空格用 keybd_event（SendInput 在 RichEdit 上丢空格）
                vk = 0x20
                scan = u32.MapVirtualKeyW(vk, 0)
                u32.keybd_event(vk, scan, 0, 0)
                u32.keybd_event(vk, scan, 0x0002, 0)
            else:
                _send_input_char(char)
            time.sleep(MAX_TEXT_CHAR_INTERVAL)

    def _pywinauto_type_keys(self, window_title, text):
        """用 pywinauto 的 type_keys 输入文本。
        优势：set_focus() 可靠激活窗口，type_keys(with_spaces=True) 正确处理空格。
        借鉴 pywinauto 最佳实践：backend="uia" + title_re 正则匹配 + set_focus + type_keys。"""
        try:
            from pywinauto.application import Application
            from pywinauto import Desktop
            import re as _re

            desktop = Desktop(backend="uia")
            dlg = None

            # 策略1: 用已有的 _find_window_by_title 找到 hwnd（已处理类名匹配等）
            #        再用 pywinauto Desktop 连接到该 hwnd
            if window_title:
                target_hwnd = _find_window_by_title(window_title)
                if target_hwnd:
                    dlg = desktop.window(handle=target_hwnd)

            # 策略2: 正则匹配标题
            if not dlg and window_title:
                title_re = None
                if " - " in window_title:
                    suffix = window_title[window_title.rfind(" - "):]
                    title_re = _re.escape(suffix) + r"$"
                else:
                    title_re = _re.escape(window_title)
                try:
                    dlg = desktop.window(title_re=title_re)
                    dlg.wait('exists', timeout=1)
                except Exception:
                    dlg = None

            # 策略3: 用 Application.connect 连接到 Notepad 进程
            #        （Desktop 有时找不到 ApplicationFrameHost 下的窗口）
            if not dlg and window_title:
                try:
                    from pywinauto.application import Application
                    # 尝试通过路径连接 Notepad
                    app = Application(backend="uia").connect(path=r"C:\Windows\System32\notepad.exe")
                    dlg = app.top_window()
                    dlg.wait('exists', timeout=2)
                except Exception:
                    dlg = None

            # 策略3: 无标题或匹配失败，用当前前台窗口
            if not dlg:
                hwnd = win32gui.GetForegroundWindow()
                dlg = desktop.window(handle=hwnd)

            # 先激活窗口（set_focus 比 wait('ready') 更可靠地确保窗口可用）
            # 先用 win32 _force_foreground 强制激活窗口（比 pywinauto set_focus 更可靠）
            target_hwnd = None
            if window_title:
                target_hwnd = _find_window_by_title(window_title)
                if target_hwnd:
                    _force_foreground(target_hwnd)
                    time.sleep(0.5)
            try:
                dlg.set_focus()
            except Exception:
                pass
            # 等待窗口就绪（不 return False，继续尝试找 Edit 控件）
            try:
                dlg.wait('ready', timeout=2)
            except Exception:
                log.info("  pywinauto: 窗口未就绪 %r，继续尝试找 Edit", window_title)
            # 验证窗口确实是前台
            fg_hwnd = win32gui.GetForegroundWindow()
            try:
                dlg_hwnd = dlg.handle
            except Exception:
                dlg_hwnd = target_hwnd or 0
            if dlg_hwnd and fg_hwnd != dlg_hwnd:
                log.info("  pywinauto: 窗口未在前台 (fg=%s, dlg=%s)，再试一次", fg_hwnd, dlg_hwnd)
                _force_foreground(dlg_hwnd)
                time.sleep(0.3)
                try:
                    dlg.set_focus()
                except Exception:
                    pass

            # 找到 Edit 控件（多种策略）
            edit = None
            # 策略1: control_type="Edit" 或 "Document"（Win11 记事本可能是 Document）
            for ct in ("Edit", "Document", "Text"):
                try:
                    edit = dlg.child_window(control_type=ct)
                    edit.wait('ready', timeout=1)
                    if edit:
                        break
                except Exception:
                    edit = None
            # 策略2: 按类名查找（RichEditD2DPT 等）
            if not edit:
                for cls in EDIT_CLASSES:
                    try:
                        edit = dlg.child_window(class_name=cls)
                        edit.wait('ready', timeout=1)
                        break
                    except Exception:
                        edit = None
            # 策略3: 用 UIA 直接从 target_hwnd 查找 EditControl（不依赖 pywinauto 控件树）
            if not edit:
                try:
                    import uiautomation as ua
                    # 用 target_hwnd（而非 GetForegroundWindow，因为前台可能是 IDE）
                    uia_hwnd = target_hwnd or dlg_hwnd or win32gui.GetForegroundWindow()
                    root = ua.ControlFromHandle(uia_hwnd)
                    if root:
                        ec = root.EditControl(searchDepth=10)
                        if ec.Exists(1.0):
                            log.info("  pywinauto: 用 UIA 找到 EditControl")
                            # 用 UIA 直接操作（跳过 pywinauto type_keys）
                            ec.SetFocus()
                            ec.Click()
                            time.sleep(0.2)
                            # 关闭可能的菜单
                            focused = ua.GetFocusedControl()
                            if focused and focused.ControlTypeName in ("MenuItemControl", "MenuBarControl"):
                                for _ in range(3):
                                    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
                                    ctypes.windll.user32.keybd_event(0x1B, 0, 0x0002, 0)
                                    time.sleep(0.1)
                                time.sleep(0.2)
                                ec.SetFocus()
                                time.sleep(0.1)
                            # Ctrl+End 定位到末尾
                            from pywinauto.keyboard import send_keys
                            send_keys("^({END})")
                            time.sleep(0.05)
                            # 逐字符输入（避免 SendKeys 特殊字符问题）
                            for char in text:
                                if self._stopped:
                                    break
                                if char == '\n':
                                    ec.SendKeys("{Enter}")
                                elif char == '\t':
                                    ec.SendKeys("{Tab}")
                                elif char in '{}[]()^%~+':
                                    ec.SendKeys("{%s}" % char)
                                else:
                                    ec.SendKeys(char, interval=0.02)
                            log.info("  UIA SendKeys 输入成功（pywinauto 回退）")
                            return True
                except Exception:
                    pass
            if not edit:
                log.info("  pywinauto: 未找到 Edit 控件")
                return False

            # 激活窗口 + 聚焦编辑控件
            try:
                dlg.set_focus()
                edit.set_focus()
            except Exception:
                pass
            time.sleep(0.2)

            # 如果焦点在菜单上，先按 Escape 关闭
            import uiautomation as ua
            focused = ua.GetFocusedControl()
            if focused and focused.ControlTypeName in ("MenuItemControl", "MenuBarControl"):
                log.info("  pywinauto: 焦点在菜单上，按 Escape 关闭")
                for _ in range(3):
                    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(0x1B, 0, 0x0002, 0)
                    time.sleep(0.1)
                time.sleep(0.2)
                try:
                    edit.set_focus()
                except Exception:
                    pass
                time.sleep(0.1)

            # 定位到文本末尾（Ctrl+End）
            from pywinauto.keyboard import send_keys
            send_keys("^({END})")
            time.sleep(0.05)

            # type_keys 输入文本（with_spaces=True 确保空格不被忽略）
            try:
                edit.type_keys(text, with_spaces=True, pause=0.02)
                log.info("  pywinauto type_keys 成功: %r", text[:30])
                return True
            except Exception as e:
                log.debug("  pywinauto type_keys 失败: %s，尝试 set_text", e)
                # set_text 回退：获取已有文本 + 追加新文本，不需要前台焦点
                try:
                    existing = edit.window_text() or ""
                    edit.set_text(existing + text)
                    log.info("  pywinauto set_text 追加成功 (已有%d字符)", len(existing))
                    return True
                except Exception as e2:
                    log.debug("  pywinauto set_text 也失败: %s", e2)
                    return False
        except Exception as e:
            log.debug("  pywinauto type_keys 失败: %s", e)
            return False

    def _try_uia_input_text(self, hwnd, text):
        """用 UIA 输入文本，适用于 Win11 记事本 RichEditD2DPT 等 PostMessage WM_CHAR 无效的控件。
        策略：ValuePattern 追加（获取已有值+新文本，SetValue 回去）> SendKeys 逐字符。
        UIA 可操作后台窗口，不依赖前台焦点。"""
        try:
            import uiautomation as ua
            root = ua.ControlFromHandle(hwnd)
            if not root:
                log.info("  UIA: ControlFromHandle 失败 hwnd=%s", hwnd)
                return False
            # 直接从目标窗口找 Edit 控件（不依赖 GetFocusedControl，因为前台可能是 IDE）
            edit_ctrl = None
            # 策略1: 从目标窗口的控件树找 Edit/Document（无深度限制）
            for ct in ("EditControl", "DocumentControl", "TextControl"):
                try:
                    ec = getattr(root, ct)()  # 无 searchDepth 限制
                    if ec.Exists(1.5):
                        edit_ctrl = ec
                        log.info("  UIA: 找到 %s name=%s", ct, ec.Name[:50] if ec.Name else "")
                        break
                except Exception:
                    pass
            # 策略2: 如果只找到 DocumentControl，尝试在其子控件中找 EditControl
            if not edit_ctrl or edit_ctrl.ControlTypeName == "DocumentControl":
                try:
                    doc = edit_ctrl or root.DocumentControl()
                    if doc.Exists(0.5):
                        child_edit = doc.EditControl()
                        if child_edit.Exists(1.0):
                            edit_ctrl = child_edit
                            log.info("  UIA: 在 DocumentControl 中找到 EditControl")
                except Exception:
                    pass
            if not edit_ctrl:
                log.info("  UIA: 未找到 EditControl")
                return False

            # 方式1: ValuePattern 追加模式（最可靠，可操作后台窗口，无特殊字符问题）
            try:
                vp = edit_ctrl.GetValuePattern()
                if vp:
                    try:
                        existing = vp.Value or ""
                    except:
                        existing = ""
                    # 追加新文本到末尾
                    vp.SetValue(existing + text)
                    log.info("  UIA ValuePattern 追加成功 (已有%d字符)", len(existing))
                    return True
                else:
                    log.info("  UIA: ValuePattern 不可用")
            except Exception as e:
                log.info("  UIA ValuePattern 失败: %s", e)

            # 方式2: SendKeys 逐字符输入（需要窗口在前台，否则发送到错误窗口）
            # 检查目标窗口是否在前台
            fg_hwnd = win32gui.GetForegroundWindow()
            try:
                dlg_hwnd = win32gui.GetParent(hwnd) or hwnd
            except:
                dlg_hwnd = hwnd
            if fg_hwnd != hwnd and fg_hwnd != dlg_hwnd:
                log.info("  UIA: 窗口不在前台(fg=%s, target=%s)，跳过 SendKeys", fg_hwnd, hwnd)
                return False

            try:
                edit_ctrl.SetFocus()
                time.sleep(0.1)
                # 用 keybd_event 发 Ctrl+End 定位到末尾
                u32 = ctypes.windll.user32
                u32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
                u32.keybd_event(0x23, 0, 0, 0)  # End down
                u32.keybd_event(0x23, 0, 2, 0)  # End up
                u32.keybd_event(0x11, 0, 2, 0)  # Ctrl up
                time.sleep(0.05)
                # 逐字符输入（用 keybd_event 处理空格，避免 SendKeys 空格丢失）
                u32 = ctypes.windll.user32
                for char in text:
                    if self._stopped:
                        break
                    if char == '\n':
                        edit_ctrl.SendKeys("{Enter}")
                    elif char == '\t':
                        edit_ctrl.SendKeys("{Tab}")
                    elif char == ' ':
                        # 空格用 keybd_event（SendKeys 在某些控件上丢空格）
                        vk = 0x20
                        scan = u32.MapVirtualKeyW(vk, 0)
                        u32.keybd_event(vk, scan, 0, 0)
                        u32.keybd_event(vk, scan, 0x0002, 0)
                        time.sleep(0.02)
                    elif char in '{}[]()^%~+':
                        edit_ctrl.SendKeys("{%s}" % char)
                    else:
                        edit_ctrl.SendKeys(char, interval=0.02)
                log.info("  UIA SendKeys 输入成功")
                return True
            except Exception as e:
                log.debug("  UIA SendKeys 失败: %s", e)
        except Exception as e:
            log.debug("  UIA 输入失败: %s", e)
        return False

    def _try_uia_value_pattern(self, hwnd, text):
        """用 UIA ValuePattern 设置文本（适用于 Chromium 等应用）"""
        try:
            import uiautomation as ua
            root = ua.ControlFromHandle(hwnd)
            if not root:
                return False
            # 找到焦点控件
            focused = ua.GetFocusedControl()
            if focused:
                try:
                    vp = focused.GetValuePattern()
                    if vp:
                        vp.SetValue(text)
                        log.info("  UIA ValuePattern 设置成功")
                        return True
                except Exception:
                    pass
            # 搜索 Edit 控件
            edit = root.EditControl(searchDepth=32)
            if edit.Exists(1.0):
                try:
                    vp = edit.GetValuePattern()
                    if vp:
                        vp.SetValue(text)
                        log.info("  UIA ValuePattern(EditControl) 设置成功")
                        return True
                except Exception:
                    pass
        except Exception as e:
            log.info("  UIA ValuePattern 失败: %s", e)
        return False

    def _do_key_press(self, step):
        key = step.get("key", "")
        if not key:
            return
        # 对 Enter 键，优先尝试 UIA 发送（Chromium 兼容）
        if key.lower() in ("enter", "return"):
            if self._try_uia_enter():
                return
        vks = _parse_key_combo(key, VK_MAP)
        if not vks:
            return
        # keybd_event 逐个按下/抬起（带延时）：SendInput 注入在 UWP 上不被处理
        u32 = ctypes.windll.user32
        for vk in vks:
            scan = u32.MapVirtualKeyW(vk, 0)
            u32.keybd_event(vk, scan, 0, 0)
            time.sleep(0.06)
        for vk in reversed(vks):
            u32.keybd_event(vk, 0, 2, 0)
            time.sleep(0.06)
        # 释放扩展修饰键
        _release_keys([0xA2, 0xA3, 0xA0, 0xA1, 0xA4, 0xA5])
        # UWP 等应用的输入管线延迟处理注入事件，等待消化再继续下一步
        time.sleep(1.0)

    def _try_uia_enter(self):
        """用 UIA 对焦点控件发送 Enter"""
        try:
            import uiautomation as ua
            focused = ua.GetFocusedControl()
            if focused:
                # 尝试 InvokePattern
                try:
                    focused.GetInvokePattern().Invoke()
                    log.info("  UIA Invoke 成功")
                    return True
                except Exception:
                    pass
                # 用 SendKeys 发送 Enter
                try:
                    focused.SendKeys("{Enter}")
                    log.info("  UIA SendKeys Enter 成功")
                    return True
                except Exception:
                    pass
        except Exception as e:
            log.info("  UIA Enter 失败: %s", e)
        return False

    def _do_scroll(self, step):
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.05)
        delta = step.get("delta", -120)
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, delta, 0)

    def _do_drag(self, step):
        sx, sy = self._resolve_coords(step)
        ex, ey = step.get("end_x"), step.get("end_y")
        if sx is None or sy is None or ex is None or ey is None:
            return
        ctypes.windll.user32.SetCursorPos(sx, sy)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
        steps = 10
        for i in range(1, steps + 1):
            if self._stopped:
                break
            px = int(sx + (ex - sx) * i / steps)
            py = int(sy + (ey - sy) * i / steps)
            ctypes.windll.user32.SetCursorPos(px, py)
            time.sleep(0.03)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


# ============ 模块级工具 ============

def _check_foreground():
    try:
        fg = win32gui.GetForegroundWindow()
        log.info("[回放] 前台窗口: hwnd=%s title=%r", fg, win32gui.GetWindowText(fg))
    except Exception:
        pass


def _force_foreground(hwnd):
    """强制目标窗口到前台（AttachThreadInput + ALT trick + 验证）"""
    try:
        # 验证窗口句柄有效
        if not win32gui.IsWindow(hwnd):
            log.info("强制前台失败: 窗口句柄无效 %s", hwnd)
            return False
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True
        _, fg_tid = win32process.GetWindowThreadProcessId(fg)
        _, target_tid = win32process.GetWindowThreadProcessId(hwnd)
        # ALT trick：让系统认为用户在操作，绕过 SetForegroundWindow 限制
        u32 = ctypes.windll.user32
        u32.keybd_event(0x12, 0, 0, 0)  # ALT down
        u32.keybd_event(0x12, 0, 0x0002, 0)  # ALT up
        if fg_tid != target_tid:
            u32.AttachThreadInput(fg_tid, target_tid, True)
        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        if fg_tid != target_tid:
            u32.AttachThreadInput(fg_tid, target_tid, False)
        time.sleep(0.5)
        # 确保 ALT 键已释放（防止 ALT 卡住导致后续空格打开系统菜单）
        u32.keybd_event(0x12, 0, 0x0002, 0)
        # 验证是否成功
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        # 再试一次
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception as e:
        log.info("强制前台失败: %s", e)
        return False


def _release_keys(vks):
    for vk in vks:
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


# ============ SendInput 实现 ============

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("_input",)
    _fields_ = [("type", ctypes.c_ulong),
                ("_input", _INPUT_UNION)]


def _send_input_keys(vk_list, key_up=True):
    """用 SendInput 发送多个按键（按下），可选释放"""
    inputs = []
    for vk in vk_list:
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        i = _INPUT()
        i.type = _INPUT_KEYBOARD
        i.ki.wVk = vk
        i.ki.wScan = scan
        i.ki.dwFlags = 0
        inputs.append(i)
    if key_up:
        for vk in reversed(vk_list):
            scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
            i = _INPUT()
            i.type = _INPUT_KEYBOARD
            i.ki.wVk = vk
            i.ki.wScan = scan
            i.ki.dwFlags = _KEYEVENTF_KEYUP
            inputs.append(i)
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))


def _send_input_char(char):
    """用 SendInput 发送单个字符（带 Shift 如果需要）"""
    vk_scan = ctypes.windll.user32.VkKeyScanW(ord(char))
    vk = vk_scan & 0xFF
    shift = (vk_scan >> 8) & 0x01
    vks = []
    if shift:
        vks.append(0x10)
    vks.append(vk)
    # 按下
    _send_input_keys(vks, key_up=False)
    time.sleep(0.01)
    # 释放
    for vk in reversed(vks):
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        i = _INPUT()
        i.type = _INPUT_KEYBOARD
        i.ki.wVk = vk
        i.ki.wScan = scan
        i.ki.dwFlags = _KEYEVENTF_KEYUP
        ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(_INPUT))
        time.sleep(0.01)


def _window_exists(title):
    """按标题查找窗口是否存在（对部分匹配做兼容）"""
    if not title:
        return False
    return bool(_find_window_by_title(title))


def _find_window_by_title(title):
    """查找窗口：精确匹配 > 包含匹配 > 后缀匹配 > 类名匹配
    应对动态标题（如 Notepad "*xxx - Notepad" / "yyy.txt - Notepad" / "记事本"）"""
    if not title:
        return None
    # 1. 精确匹配
    h = win32gui.FindWindow(None, title)
    if h:
        return h
    found = []

    # 从标题提取应用名，用于类名匹配兜底
    # "xxx - Notepad" -> class "Notepad"; "xxx - 计算器" -> class "CalcFrame"
    _app_class_hints = {
        "Notepad": "Notepad",
        "Calculator": "CalcFrame",
        "计算器": "CalcFrame",
    }
    app_class = None
    for app_name, cls_name in _app_class_hints.items():
        if app_name in title:
            app_class = cls_name
            break

    def cb(w, l):
        try:
            wtext = win32gui.GetWindowText(w)
            if wtext:
                # 2. 包含匹配（录制标题是实际标题的子串，或反过来）
                if title in wtext or wtext in title:
                    l.append(w)
                    return True
                # 3. 后缀匹配：提取应用名（如 "- Notepad"），匹配同应用窗口
                if " - " in title:
                    suffix = title[title.rfind(" - "):]
                    if len(suffix) >= 5 and wtext.endswith(suffix):
                        l.append(w)
                        return True
            # 4. 类名匹配兜底：Win11 记事本标题可能是 "记事本"（无文件名后缀）
            if app_class:
                cls = win32gui.GetClassName(w)
                if cls == app_class and win32gui.IsWindowVisible(w):
                    l.append(w)
                    return True
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, found)
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    # 多个匹配窗口时，优先选择最近激活的（Z 序最前的）
    # 按窗口在 Z 序中的位置排序（最靠前的优先）
    def z_order(hwnd):
        try:
            # GetWindow with GW_HWNDPREV 返回 Z 序中前面的窗口
            # 简单方法：用 IsWindowVisible + GetForegroundWindow 优先
            if hwnd == win32gui.GetForegroundWindow():
                return 0  # 前台窗口最优先
            # 检查窗口是否最小化
            if win32gui.IsIconic(hwnd):
                return 2  # 最小化的排最后
            return 1
        except:
            return 3
    found.sort(key=z_order)
    return found[0]


def _mouse_click(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.1)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.1)


def _mouse_right_click(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.1)
    ctypes.windll.user32.mouse_event(0x0008, 0, 0, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.mouse_event(0x0010, 0, 0, 0, 0)
    time.sleep(0.3)


# ============ PostMessage 后台注入（借鉴 ScreenClaw） ============
# 向目标窗口直接发送鼠标消息，不抢物理鼠标，不依赖窗口在前台
# 坐标需转换为客户区坐标（virtual coords），DPI 缩放由系统处理

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002


def _make_lparam(x, y):
    """构造 PostMessage lParam：低 16 位 X，高 16 位 Y"""
    return (y << 16) | (x & 0xFFFF)


def _post_message_click(hwnd, screen_x, screen_y):
    """PostMessage 后台左键点击（ScreenClaw 方式）。
    屏幕坐标转客户区坐标后发送 WM_LBUTTONDOWN/UP。
    返回 True 成功，False 失败（调用方应回退前台点击）。"""
    try:
        cx, cy = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
        lparam = _make_lparam(cx, cy)
        u32 = ctypes.windll.user32
        u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(0.05)
        u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
        return True
    except Exception as e:
        log.debug("PostMessage 点击失败: %s", e)
        return False


def _post_message_right_click(hwnd, screen_x, screen_y):
    """PostMessage 后台右键点击"""
    try:
        cx, cy = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
        lparam = _make_lparam(cx, cy)
        u32 = ctypes.windll.user32
        u32.PostMessageW(hwnd, WM_RBUTTONDOWN, MK_RBUTTON, lparam)
        time.sleep(0.05)
        u32.PostMessageW(hwnd, WM_RBUTTONUP, 0, lparam)
        return True
    except Exception as e:
        log.debug("PostMessage 右键失败: %s", e)
        return False


def _find_edit_child(hwnd):
    """在窗口的子控件中查找 Edit/RichEdit 控件（用于 EM_REPLACESEL 后台输入）"""
    found = []
    def cb(h, l):
        try:
            cls = win32gui.GetClassName(h)
            if "edit" in cls.lower() or "rich" in cls.lower():
                l.append(h)
        except Exception:
            pass
        return True
    win32gui.EnumChildWindows(hwnd, cb, found)
    return found[0] if found else None


def _find_focused_edit(hwnd):
    """找到前台线程的焦点控件，向上追溯 Edit；失败回退第一个 Edit 子控件"""
    try:
        _, tid = win32process.GetWindowThreadProcessId(hwnd)
        info = win32gui.GetGUIThreadInfo(tid)
        focus = info.get("hwndFocus") or info.get("hWndFocus")
        h = focus
        while h:
            try:
                cls = win32gui.GetClassName(h) or ""
            except Exception:
                cls = ""
            if "edit" in cls.lower() or "rich" in cls.lower():
                return h
            h = win32gui.GetParent(h)
    except Exception:
        pass
    return _find_first_edit(hwnd)


def _find_first_edit(hwnd):
    found = []

    def cb(h, l):
        found.append(h)
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        return None
    for ch in found:
        try:
            cls = win32gui.GetClassName(ch)
        except Exception:
            continue
        if cls in EDIT_CLASSES:
            return ch
    return None


def _find_focused_edit_uia():
    """用 UIA 查找焦点 EditControl 的原生句柄（WPS/Qt 等自绘控件）"""
    try:
        import uiautomation as ua
        focused = ua.GetFocusedControl()
        if focused and focused.ControlTypeName in ("EditControl", "ComboBoxControl"):
            hwnd = focused.NativeWindowHandle
            if hwnd:
                return hwnd
    except Exception:
        pass
    return None


def _parse_key_combo(key, vk_map):
    parts = key.lower().split()
    vks = []
    for part in parts:
        if part in vk_map:
            vks.append(vk_map[part])
        elif len(part) == 1:
            vks.append(ord(part.upper()))
    return vks


def _control_type_by_name(name):
    """ControlTypeName 字符串 -> ControlType 枚举值"""
    import uiautomation as ua
    mapping = {
        "ButtonControl": ua.ControlType.ButtonControl,
        "EditControl": ua.ControlType.EditControl,
        "ListItemControl": ua.ControlType.ListItemControl,
        "TreeItemControl": ua.ControlType.TreeItemControl,
        "MenuItemControl": ua.ControlType.MenuItemControl,
        "ComboBoxControl": ua.ControlType.ComboBoxControl,
        "CheckBoxControl": ua.ControlType.CheckBoxControl,
        "RadioButtonControl": ua.ControlType.RadioButtonControl,
        "HyperlinkControl": ua.ControlType.HyperlinkControl,
        "TabItemControl": ua.ControlType.TabItemControl,
        "DocumentControl": ua.ControlType.DocumentControl,
        "WindowControl": ua.ControlType.WindowControl,
        "PaneControl": ua.ControlType.PaneControl,
        "CustomControl": ua.ControlType.CustomControl,
    }
    return mapping.get(name, None)