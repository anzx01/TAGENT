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
from services.input import InputEngine
from services.input.web_channel import get_web_channel

log = get_logger("player")

RELEASE_MODIFIERS = [0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C]
HUMAN_WAIT_TIMEOUT = 120.0
ACTION_RETRIES = 4
ACTION_RETRY_DELAY = 1.0

# 移植 openskills replay/engine.ts：定位重试与步骤验证
LOCATOR_RETRIES = 3      # 定位失败重试次数
LOCATOR_RETRY_DELAY = 1.2  # 重试间隔（秒）

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
        self._sort_locators()
        self._stopped = False
        self.variables = variables or {}
        self.var_sources = var_sources or {}  # 阶段2.1：变量来源绑定
        self.on_pause = None  # 阶段3：等待人工接管时的回调（state 更新）
        self._paused = False
        self._resume = None
        self._resume_action = None
        self._waiting_step = None
        self._pause_reason = ""
        self._last_pause_reason = ""
        self._session_steps = set(session_steps or [])  # 阶段2.4
        self.checkpoint_path = None  # 阶段5.1：断点续跑
        self._corrections = corrections or []  # 阶段3.3：自愈库
        self._step_results = []  # 阶段5.2：逐步结果
        self._pause_reasons = []  # 阶段5.2：暂停原因
        self.screenshot_dir = None  # 阶段1.2：回放截图目录
        self._rec_id = ""  # 录制 ID（用于重新演示此步）
        self._engine = InputEngine()  # 输入注入引擎 V3

    def stop(self):
        self._stopped = True
        self._paused = False
        self._resume_cond_resume()

    def pause(self):
        """请求在当前安全边界暂停回放。

        ``pause`` 可能由 UI 线程调用，而回放线程此时通常正在等待步骤
        时间戳。仅设置标志而不在主循环检查，会让工具栏上的“暂停”看起来
        无效；主循环现在通过 ``_wait_if_paused`` 在下一次动作前阻塞。
        """
        self._paused = True

    def resume(self, retry=False):
        """人工接管完成后继续回放"""
        self._resume_action = "retry" if retry else "skip"
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
        # 回放线程内初始化 COM（UIA/剪贴板等需要），确保与主线程隔离
        import pythoncom
        _com_inited = False
        try:
            pythoncom.CoInitialize()
            _com_inited = True
        except Exception:
            pass
        # Preserve pause/stop requests issued while the worker was starting.
        self._step_results = []
        self._pause_reasons = []
        self._resume_action = None
        self._waiting_step = None
        self._pause_reason = ""
        self._last_pause_reason = ""
        if start_delay > 0:
            self._sleep_interruptible(start_delay)
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
        self._sleep_interruptible(0.2)
        prev_t = 0.0
        done = from_step
        for i, step in enumerate(self.steps[from_step:], start=from_step):
            if not self._wait_if_paused() or self._stopped:
                break
            if step.get("action") == "wait":
                self._do_wait(step, prev_t)
                prev_t = step.get("t", 0)
                continue
            wait = max(0.0, step.get("t", 0) - prev_t)
            if not self._sleep_interruptible(min(wait, max_wait)):
                break
            if not self._wait_if_paused() or self._stopped:
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
            # 阶段3.1：定位失败 -> 重试 -> 尝试纠错库 -> 尝试 LLM -> 暂停
            locator_failed = False
            step_already_executed = False
            if step.get("action") in ("click", "right_click") and step.get("locator"):
                if not self._check_locator_retry(step):
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
                            action_after_pause = self._pause_for_human(i, step_detail)
                            if action_after_pause == "retry" and not self._stopped:
                                retry_result = self._play_step(step)
                                if retry_result.ok:
                                    step_ok = True
                                    locator_failed = False
                                    step_already_executed = True
                                    step_detail = f"重试成功 ({retry_result.method})"
                            if self._stopped:
                                break
            if step_ok and not locator_failed and not step_already_executed:
                if not self._try_correction(i):
                    result = self._play_step_with_retry(step)
                    if not result.ok:
                        step_ok = False
                        hint = _web_failure_hint(step)
                        step_detail = f"注入失败: {result.reason or result.method}"
                        if hint:
                            step_detail += f"。{hint}"
                        self._pause_reasons.append(
                            {"step": i, "reason": step_detail})
                        log.warning("  步骤 %d %s，暂停等待人工", i, step_detail)
                        action_after_pause = self._pause_for_human(i, step_detail)
                        if action_after_pause == "retry" and not self._stopped:
                            retry_result = self._play_step(step)
                            if retry_result.ok:
                                step_ok = True
                                step_already_executed = True
                                step_detail = f"重试成功 ({retry_result.method})"
                        if self._stopped:
                            break
                step_detail = step_detail or "成功"
            # 步骤事后验证（移植 openskills verify，诊断不阻断）
            if step_ok and step.get("locator"):
                v = self._verify_step(step)
                if v is False:
                    step_detail = (step_detail or "成功") + " [verify_warn]"
                    log.warning("  步骤 %d verify 未通过（目标不可见）", i)
                elif v is True:
                    step_detail = (step_detail or "成功") + " [verify_ok]"
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
            if not self._sleep_interruptible(step_delay):
                break
        interrupted = self._stopped
        summary = f"完成 {done}/{len(self.steps)} 步" + (" (已中断)" if interrupted else "")
        try:
            if _com_inited:
                pythoncom.CoUninitialize()
        except Exception:
            pass
        return {"summary": summary, "steps": self._step_results,
                "pause_reasons": self._pause_reasons}

    def _wait_if_paused(self):
        """在动作边界等待手动暂停/接管结束。

        返回 False 表示收到停止请求。轮询间隔很短，且不依赖一个可能被
        COM/UIA 调用占用的 ``threading.Event``，因此 stop/resume 始终可达。
        """
        while self._paused and not self._stopped:
            time.sleep(0.1)
        return not self._stopped

    def _sleep_interruptible(self, duration):
        """可响应 pause/stop 的短睡眠，避免时间戳等待吞掉控制请求。"""
        try:
            remaining = max(0.0, float(duration))
        except (TypeError, ValueError):
            remaining = 0.0
        deadline = time.monotonic() + remaining
        while remaining > 0:
            if self._stopped:
                return False
            if self._paused and not self._wait_if_paused():
                return False
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.1, remaining))
        return not self._stopped

    def _pause_for_human(self, step_index, reason=""):
        """阶段2.4：会话步骤（登录/2FA）暂停，等人工完成"""
        self._paused = True
        self._waiting_step = step_index
        self._pause_reason = reason or "需要人工接管"
        self._last_pause_reason = self._pause_reason
        self._resume_action = None
        log.info("会话步骤 %d：等待人工登录/验证", step_index)
        deadline = time.monotonic() + HUMAN_WAIT_TIMEOUT
        while self._paused and not self._stopped:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._pause_reason = f"{self._pause_reason}（等待超时）"
                self._last_pause_reason = self._pause_reason
                self._stopped = True
                break
            time.sleep(min(0.3, remaining))
        action = self._resume_action or "skip"
        self._paused = False
        self._waiting_step = None
        self._pause_reason = ""
        return action

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

    def _sort_locators(self):
        """移植：openskills selectors.ts - 定位策略按置信度降序回退（无 confidence 保序）"""
        for step in self.steps:
            locs = step.get("locator")
            if not isinstance(locs, list) or len(locs) < 2:
                continue
            if all(l.get("confidence") is not None for l in locs):
                step["locator"] = sorted(
                    locs, key=lambda l: l.get("confidence", 0), reverse=True)

    def _check_locator_retry(self, step):
        """移植：定位失败带重试（慢渲染/窗口切换场景），仍失败返回 False"""
        for _ in range(LOCATOR_RETRIES):
            if self._check_locator(step):
                return True
            if not self._sleep_interruptible(LOCATOR_RETRY_DELAY):
                return False
        return False

    def _verify_step(self, step):
        """移植：回放后验证目标仍存在（诊断性，不阻断）。
        返回 True=验证通过 / False=warn / None=无可验证目标"""
        locs = step.get("locator", [])
        raw = (step.get("screen_x"), step.get("screen_y"))
        for loc in locs:
            if loc.get("type") in ("uia", "ocr") and loc.get("name"):
                x, y = self._try_locator(loc, raw)
                if x is None:
                    return False
                return True
        return None

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
        from services.input.common import StepResult
        if action == "click":
            return self._do_click(step)
        elif action == "double_click":
            return self._do_double_click(step)
        elif action == "right_click":
            return self._do_right_click(step)
        elif action == "input_text":
            return self._do_input_text(step)
        elif action == "key_press":
            return self._do_key_press(step)
        elif action == "scroll":
            return self._do_scroll(step)
        elif action in ("swipe", "drag"):
            return self._do_drag(step)
        elif action == "switch_window":
            ok = self._do_switch_window(step)
            if not ok:
                return self._fail("switch_window", "no_window")
        return StepResult(True, "unknown_ok")

    def _play_step_with_retry(self, step):
        """Retry transient window/page availability before handing off.

        A recorded window may take a moment to reopen or change its title.
        Waiting here makes ordinary replay self-healing while still failing
        closed for non-transient input errors.
        """
        result = self._play_step(step)
        transient = {"no_window", "element_not_found", "element_hidden", "not_connected"}
        for _ in range(ACTION_RETRIES - 1):
            if result.ok or result.reason not in transient or self._stopped:
                break
            if not self._sleep_interruptible(ACTION_RETRY_DELAY):
                break
            result = self._play_step(step)
        return result

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
        """按父窗口链定位顶级窗口。
        parents 链存在时找不到目标窗口就返回 None（绝不 fallback 前台窗口乱搜，
        避免录制时后台窗口的同名控件被误命中）；只有完全没有 parents 信息才用前台窗口。"""
        parents = loc.get("parents", [])
        if parents:
            for p in reversed(parents):
                p_name = p.get("name", "").strip()
                p_class = p.get("class", "").strip()
                hwnd = None
                if p_name:
                    hwnd = _find_window_by_title(p_name)
                if hwnd is None and p_class:
                    hwnd = win32gui.FindWindow(p_class, None)
                if hwnd:
                    return ua.ControlFromHandle(hwnd)
            return None
        fg = win32gui.GetForegroundWindow()
        return ua.ControlFromHandle(fg)

    def _find_uia_ctrl(self, loc):
        """按 uia locator 找到控件对象（pattern 操作前先取控件），失败返回 None"""
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
            if target_name:
                kw["Name"] = target_name
            if kw:
                candidates.append((kw, False))
            if target_class and target_auto_id:
                candidates.append((dict(ClassName=target_class), False))

            window = self._find_top_window(ua, loc)
            if window is None:
                return None

            for kw, _ in candidates:
                ctrl = window.Control(**kw, searchDepth=32)
                if ctrl.Exists(0.5):
                    return ctrl

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
                                return children[idx]
        except Exception as e:
            log.info("[UIA 定位失败] %s", e)
        return None

    def _locate_by_uia(self, loc, raw=(None, None)):
        """用 UI Automation 重新定位元素。多属性组合搜索，避免逐类型穷举。"""
        from services.input.engine import _timed_call
        ctrl = _timed_call(lambda: self._find_uia_ctrl(loc), timeout=2.5)
        if ctrl is None:
            return None, None
        try:
            def _point(ctrl):
                rect = ctrl.BoundingRectangle
                # 容器尺寸阈值检查：面积超过窗口 50% 时跳过（避免找到过大容器如"桌面 1"）
                ctrl_area = (rect.right - rect.left) * (rect.bottom - rect.top)
                if ctrl_area > 0:
                    try:
                        window_rect = win32gui.GetWindowRect(win32gui.GetForegroundWindow())
                        window_area = (window_rect[2] - window_rect[0]) * (window_rect[3] - window_rect[1])
                        if window_area > 0 and ctrl_area > window_area * 0.5:
                            log.debug("  UIA 跳过过大容器: 面积 %d > 窗口 50%%", ctrl_area)
                            return None, None
                    except Exception:
                        pass
                # 原始点击点落在控件内时优先使用原始坐标（大容器中心会偏离点击点）
                rx, ry = raw
                if rx is not None and ry is not None:
                    if rect.left <= rx <= rect.right and rect.top <= ry <= rect.bottom:
                        return rx, ry
                return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2

            def _checked(res):
                # 定位结果与录制坐标偏移过大时跳过该定位器（录制器可能捕获了错误元素）
                if res is None or res[0] is None or res[1] is None:
                    return None
                rx, ry = raw
                if rx is None or ry is None:
                    return res
                dx = res[0] - rx
                dy = res[1] - ry
                if dx * dx + dy * dy > 100 * 100:
                    log.info("  UIA 定位偏移过大 (%+d,%+d)px，跳过该定位器", dx, dy)
                    return None
                return res

            return _checked(_point(ctrl)) or (None, None)
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
    def _do_switch_window(self, step) -> bool:
        """切换窗口：照抄 AHK 三级回退 + pywinauto wait('ready') + 前台兜底。
        回退链：标题匹配 -> 对话框#32770 -> pywinauto -> 前台兜底
        返回 True 表示切换成功，False 表示失败"""
        title = step.get("window", "")
        hwnd = step.get("hwnd")
        if not title and not hwnd:
            return True

        # ---- 策略0: 切到桌面（Program Manager）时先真正显示桌面 ----
        # Progman 是 Z 序最底的窗口，仅置前台会被其他窗口遮挡，物理点击会点错窗口
        if title == "Program Manager":
            _show_desktop()
            time.sleep(0.4)
            if _find_window_by_title("Program Manager"):
                log.info("  已切换窗口(显示桌面): 'Program Manager'")
                return True

        # ---- 策略1: win32 标题匹配 + _force_foreground（等待 ready，最多 5s）----
        h = None
        if title:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if self._stopped:
                    return False
                h = _find_window_by_title(title)
                if h and win32gui.IsWindowVisible(h) and win32gui.IsWindowEnabled(h):
                    break
                time.sleep(0.3)
        else:
            h = hwnd

        if h and win32gui.IsWindow(h):
            if _force_foreground(h):
                log.info("  已切换窗口: %r (hwnd=%s)", title, h)
                time.sleep(0.3)
                return True
            try:
                win32gui.ShowWindow(h, 9)
                win32gui.SetForegroundWindow(h)
                log.info("  已切换窗口(SFW): %r (hwnd=%s)", title, h)
                time.sleep(0.3)
                return True
            except Exception:
                pass

        # ---- 策略2: 对话框类名 #32770 匹配（照抄 AHK ahk_class #32770）----
        if title:
            h = _find_dialog(title)
            if h and _force_foreground(h):
                log.info("  对话框已切换: %r (hwnd=%s, class=#32770)", title, h)
                time.sleep(0.3)
                return True

        # ---- 策略3: pywinauto 兜底 ----
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            dlg = None
            if title:
                for spec_fn in [
                    lambda: desktop.window(title=title),
                    lambda: desktop.window(title_re=re.escape(title.lstrip("*"))),
                    lambda: desktop.window(title_re=".*" + re.escape(title.lstrip("*")) + ".*"),
                ]:
                    try:
                        d = spec_fn()
                        d.wait('ready', timeout=2)
                        dlg = d
                        break
                    except Exception:
                        dlg = None
            else:
                dlg = desktop.window(handle=hwnd)
            if dlg:
                dlg.set_focus()
                log.info("  pywinauto 已切换窗口: %r", title)
                time.sleep(0.3)
                return True
        except Exception:
            pass

        # ---- 策略4: 前台窗口兜底（照抄 SikuliX 思想：弹窗可能已是前台）----
        fg = win32gui.GetForegroundWindow()
        if fg:
            fg_title = win32gui.GetWindowText(fg)
            if title and title not in fg_title:
                log.warning("  切换窗口失败：未找到 %r，当前前台是 %r", title, fg_title)
                return False
            log.info("  前台兜底: %r (hwnd=%s)（目标 %r 已匹配前台）",
                     fg_title, fg, title)
            return True

        log.warning("  切换窗口失败：未找到 %r（所有策略均失败）", title)
        return False

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
    # （输入注入全部委托给 services/input 引擎 V3：
    #  UIA pattern 优先 -> PostMessage 后台 -> 前台守卫物理注入，
    #  对齐 Microsoft winappCli 的 fail-fast 设计，前台不符即失败暂停而非打错窗口）

    # 桌面级窗口标题/类名：这些窗口用前台物理点击，无守卫目标
    _DESKTOP_WINDOWS = {"Program Manager", "主机弹出窗口", ""}

    def _is_desktop_action(self, step):
        """判断是否为桌面级操作（右键菜单、桌面点击等）"""
        window = step.get("window", "")
        return window in self._DESKTOP_WINDOWS or not window

    def _target_hwnd(self, window_title, recorded_hwnd=None):
        """按标题找目标窗口；找不到时回退到前台窗口（仅限同种类窗口）"""
        if recorded_hwnd:
            try:
                if win32gui.IsWindow(recorded_hwnd) and (
                        not window_title or _same_window_kind(recorded_hwnd, window_title)):
                    return recorded_hwnd
            except Exception:
                pass
        if window_title:
            hwnd = _find_window_by_title(window_title)
            if hwnd and win32gui.IsWindow(hwnd):
                return hwnd
        fg = win32gui.GetForegroundWindow()
        if fg and _same_window_kind(fg, window_title):
            return fg
        return None

    def _try_uia_operate(self, step, action_name):
        """click/double_click 优先：定位 UIA 控件后直接 pattern 操作，不模拟鼠标。
        失败返回 None，由调用方回退坐标链路。"""
        for loc in step.get("locator", []):
            if loc.get("type") != "uia":
                continue
            from services.input.engine import _timed_call
            ctrl = _timed_call(lambda: self._find_uia_ctrl(loc), timeout=2.5)
            if ctrl is None:
                return None
            if self._engine.invoke_ctrl(ctrl):
                log.info("  -> UIA %s 成功: %r (%s)", action_name, ctrl.Name,
                         getattr(ctrl, "ControlTypeName", "?"))
                return self._ok(f"uia_{action_name}")
            return None
        return None

    def _activate_target(self, hwnd):
        """确保目标窗口在前台（点击/输入前激活，避免动作落在被遮挡窗口上）"""
        try:
            if hwnd and win32gui.IsWindow(hwnd):
                fg = win32gui.GetForegroundWindow()
                if fg != hwnd:
                    _force_foreground(hwnd)
        except Exception as e:
            log.debug("激活目标窗口失败: %s", e)

    def _do_click(self, step):
        uia_result = self._try_uia_operate(step, "invoke")
        if uia_result:
            return uia_result
        # 网页通道优先（CDP 注入，不抢焦点——浏览器场景）
        web = get_web_channel()
        dom_loc = next((l for l in step.get("locator", []) if l.get("type") == "dom"), None)
        if dom_loc:
            res = web.maybe_click(step.get("window", ""), dom_loc)
            if res is not None:
                log.info("  -> 网页点击 (CDP) %r", dom_loc.get("text", ""))
                return res
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return self._fail("click", "no_coords")
        if self._is_desktop_action(step):
            log.info("  -> 桌面点击 (%d,%d)", x, y)
            return self._engine.click_desktop(x, y)
        hwnd = self._target_hwnd(step.get("window", ""), step.get("hwnd"))
        if not hwnd:
            return self._fail("click", "no_window")
        self._activate_target(hwnd)
        log.info("  -> 点击 (%d,%d) -> %r", x, y, step.get("window", ""))
        return self._engine.click(hwnd, x, y)

    def _do_double_click(self, step):
        # 双击不走 pattern 链：桌面 ListItem 的 Invoke 会抛 COMError 且 Select 只是选中，
        # "假装成功"会掩盖未打开文件的失败。物理双击（UIA 定位坐标）最忠实可靠。
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return self._fail("double_click", "no_coords")
        if self._is_desktop_action(step):
            log.info("  -> 桌面双击 (%d,%d)", x, y)
            return self._engine.double_click_desktop(x, y)
        hwnd = self._target_hwnd(step.get("window", ""), step.get("hwnd"))
        if not hwnd:
            return self._fail("double_click", "no_window")
        self._activate_target(hwnd)
        log.info("  -> 双击 (%d,%d) -> %r", x, y, step.get("window", ""))
        return self._engine.double_click(hwnd, x, y)

    def _do_right_click(self, step):
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return self._fail("right_click", "no_coords")
        if self._is_desktop_action(step):
            log.info("  -> 桌面右键 (%d,%d)", x, y)
            self._engine.fg._mouse_right_click(x, y)
            time.sleep(0.5)
            return self._ok("desktop_right_click")
        hwnd = self._target_hwnd(step.get("window", ""), step.get("hwnd"))
        if not hwnd:
            return self._fail("right_click", "no_window")
        self._activate_target(hwnd)
        log.info("  -> 右键 (%d,%d) -> %r", x, y, step.get("window", ""))
        return self._engine.right_click(hwnd, x, y)

    def _do_input_text(self, step):
        text = self._render_text(step.get("text", ""))
        if not text:
            return self._ok("skip")
        # 网页输入通道（仅当页内已点入目标）
        web = get_web_channel()
        win = step.get("window", "")
        res = web.maybe_type(win, text)
        if res is not None:
            log.info("  -> 网页输入 (CDP) %r", text[:30])
            return res
        hwnd = self._target_hwnd(win, step.get("hwnd"))
        if not hwnd:
            return self._fail("input_text", "no_window")
        self._activate_target(hwnd)
        log.info("  输入: %r -> %r", text[:50], win32gui.GetWindowText(hwnd)[:40])
        return self._engine.type_text(hwnd, text)

    def _ok(self, method):
        from services.input.common import StepResult
        return StepResult(True, method)

    def _fail(self, method, reason):
        from services.input.common import StepResult
        return StepResult(False, method, reason)

    def _uia_click_edit(self, hwnd_or_title):
        """用 UIA 找到编辑控件并点击，确保焦点在编辑区。"""
        try:
            import uiautomation as ua
            target_hwnd = None
            if isinstance(hwnd_or_title, int):
                target_hwnd = hwnd_or_title
            elif hwnd_or_title:
                target_hwnd = _find_window_by_title(hwnd_or_title)
            if not target_hwnd:
                target_hwnd = win32gui.GetForegroundWindow()
            root = ua.ControlFromHandle(target_hwnd)
            if not root:
                return False
            for ct in ("EditControl", "DocumentControl", "TextControl"):
                edit = getattr(root, ct)(searchDepth=10)
                if edit.Exists(1.0):
                    edit.SetFocus()
                    edit.Click()
                    return True
            return False
        except Exception:
            return False

    def _do_key_press(self, step):
        key = step.get("key", "")
        if not key:
            return self._ok("skip")
        # 网页按键通道
        web = get_web_channel()
        res = web.maybe_key(step.get("window", ""), key)
        if res is not None:
            log.info("  -> 网页按键 (CDP) %r", key)
            return res
        hwnd = self._target_hwnd(step.get("window", ""), step.get("hwnd"))
        if not hwnd:
            return self._fail("key_press", "no_window")
        log.info("  按键: %r -> %r", key, win32gui.GetWindowText(hwnd)[:40])
        return self._engine.key_press(hwnd, key)

    def _do_scroll(self, step):
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return self._fail("scroll", "no_coords")
        delta = step.get("delta", -120)
        self._engine.scroll(x, y, delta)
        return self._ok("scroll")

    def _do_drag(self, step):
        sx, sy = self._resolve_coords(step)
        ex, ey = step.get("end_x"), step.get("end_y")
        if sx is None or sy is None or ex is None or ey is None:
            return self._fail("drag", "no_coords")
        self._engine.drag(sx, sy, ex, ey, lambda: self._stopped)
        return self._ok("drag")


# ============ 模块级工具 ============

def _web_failure_hint(step) -> str:
    """浏览器步骤注入失败时给出网页通道提示（避免盲目等待人工）"""
    win = step.get("window", "")
    if not win or not any(k in win for k in ("Chrome", "Edge", "Brave", "Firefox")):
        return ""
    channel = get_web_channel()
    if not channel.ensure():
        return ("浏览器需以 --remote-debugging-port=9222 启动，"
                "网页链接入才能离线注入（不受前台限制）；当前未检测到调试端口")
    if not channel.can_inject(win):
        return "网页通道已连接，但未匹配到该窗口对应的页面（可能非当前标签页）"
    return "网页通道可用但定位失败（缺少 dom 定位器）"

def _check_foreground():
    try:
        fg = win32gui.GetForegroundWindow()
        log.info("[回放] 前台窗口: hwnd=%s title=%r", fg, win32gui.GetWindowText(fg))
    except Exception:
        pass


def _show_desktop():
    """真正显示桌面：最小化所有覆盖桌面的可见顶层窗口（Progman/任务栏除外）。
    仅置 Progman 前台无法露出桌面（Z 序最底），物理点击会被其他窗口拦截。"""
    try:
        import win32con
        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            cls = win32gui.GetClassName(hwnd)
            if cls in ("Progman", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title or title in ("Program Manager", "任务切换"):
                return True
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            except Exception:
                pass
            return True
        win32gui.EnumWindows(_cb, None)
        time.sleep(0.3)
        progman = win32gui.FindWindow("Progman", "Program Manager")
        if progman:
            win32gui.ShowWindow(progman, 5)  # SW_SHOW
            _force_foreground(progman)
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


def _window_exists(title):
    """按标题查找窗口是否存在（对部分匹配做兼容）"""
    if not title:
        return False
    return bool(_find_window_by_title(title))


def _find_window_by_title(title):
    """查找窗口（照抄 AHK SetTitleMatchMode + winappCli fail-fast 精神）。
    匹配优先级：精确 > 去星精确 > 单向包含 > 类名+进程名（兜底）。
    类名兜底命中多个窗口时拒绝猜测（返回 None），避免打错窗口。"""
    if not title:
        return None
    title_clean = title.lstrip("*") if title.startswith("*") else title

    # 1. 精确匹配
    h = win32gui.FindWindow(None, title)
    if h:
        return h
    if title_clean != title:
        h = win32gui.FindWindow(None, title_clean)
        if h:
            return h

    # 应用提示表（AHK ahk_class + ahk_exe 组合做法）
    _app_hints = {
        "Notepad": ("Notepad", "notepad.exe"),
        "记事本": ("Notepad", "notepad.exe"),
        "Calculator": ("CalcFrame", "calc.exe"),
        "计算器": ("CalcFrame", "calc.exe"),
        "另存为": ("#32770", None),
        "Save As": ("#32770", None),
        "打开": ("#32770", None),
        "Open": ("#32770", None),
        "确认": ("#32770", None),
    }
    hint_class = None
    hint_exe = None
    for app_name, (cls, exe) in _app_hints.items():
        if app_name in title:
            hint_class = cls
            hint_exe = exe
            break

    found = []

    def cb(w, l):
        try:
            if not win32gui.IsWindowVisible(w):
                return True
            wtext = win32gui.GetWindowText(w)
            if not wtext:
                return True
            wtext_clean = wtext.lstrip("*") if wtext.startswith("*") else wtext

            # 2. 单向包含匹配（AHK Mode 2：title in wtext，不做反向）
            if title_clean and len(title_clean) >= 4 and title_clean in wtext_clean:
                l.append((2, w))
                return True

            # 3. 类名 + 进程名匹配（AHK ahk_class + ahk_exe 组合，兜底）
            if hint_class:
                cls = win32gui.GetClassName(w)
                if cls == hint_class:
                    if hint_exe:
                        _, pid = win32process.GetWindowThreadProcessId(w)
                        try:
                            import psutil
                            p = psutil.Process(pid)
                            if p.name().lower() == hint_exe.lower():
                                l.append((1, w))
                                return True
                        except Exception:
                            l.append((1, w))
                            return True
                    else:
                        l.append((1, w))
                        return True
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, found)
    if not found:
        return None
    best = max(lv for lv, _ in found)
    cands = [w for lv, w in found if lv == best]
    if best == 1 and len(cands) > 1:
        # 类名兜底命中多个同进程窗口：拒绝猜测，交给调用方回退前台
        return None
    if len(cands) == 1:
        return cands[0]
    # 同级多个匹配：Z 序最前的优先（前台 > 可见 > 最小化）
    def z_order(hwnd):
        try:
            if hwnd == win32gui.GetForegroundWindow():
                return 0
            if win32gui.IsIconic(hwnd):
                return 2
            return 1
        except:
            return 3
    cands.sort(key=z_order)
    return cands[0]


def _same_window_kind(hwnd, title):
    """判断窗口与录制标题是否同种类（稳定后缀或类名一致）"""
    try:
        if not title:
            return True
        wtext = win32gui.GetWindowText(hwnd)
        if not wtext:
            return False
        for a, b in ((title, wtext), (wtext, title)):
            ti = a.rfind(" - ")
            if ti != -1 and b.endswith(a[ti:]):
                return True
        return False
    except Exception:
        return False


def _find_dialog(title=None):
    """专门查找标准 Win32 对话框（类名 #32770）。
    照抄 AHK WinWait ahk_class #32770，对话框标题可能因语言不同，用类名兜底。"""
    found = []

    def cb(w, l):
        try:
            if not win32gui.IsWindowVisible(w):
                return True
            cls = win32gui.GetClassName(w)
            if cls == "#32770":
                if not title:
                    l.append(w)
                    return True
                wtext = win32gui.GetWindowText(w)
                if title in wtext:
                    l.append(w)
                    return True
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, found)
    return found[0] if found else None


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
