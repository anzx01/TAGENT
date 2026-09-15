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
WAIT_HARD_TIMEOUT = 15.0

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
                 corrections=None, var_sources=None, run_context=None):
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
        self._desktop_menu_open = False
        self._session_steps = set(session_steps or [])  # 阶段2.4
        self.checkpoint_path = None  # 阶段5.1：断点续跑
        self._corrections = corrections or []  # 阶段3.3：自愈库
        # A replay receives a detached immutable snapshot from the server.  The
        # legacy file path remains available for correction storage, but edits
        # made to task.json while this run is active cannot change ``steps``.
        self._run_context = run_context
        if run_context is not None:
            try:
                import copy
                self.steps = copy.deepcopy(run_context.steps)
                self._sort_locators()
            except Exception:
                # A malformed context must not make old callers unusable.
                log.warning("invalid run context; using task file snapshot")
        self._step_results = []  # 阶段5.2：逐步结果
        self._pause_reasons = []  # 阶段5.2：暂停原因
        self._agent_suggestions = []  # bounded suggestions; never executed in-run
        self._last_correction_id = ""
        self._last_correction_success = False
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
        self._agent_suggestions = []
        self._last_correction_id = ""
        self._last_correction_success = False
        self._resume_action = None
        self._waiting_step = None
        self._pause_reason = ""
        self._last_pause_reason = ""
        self._desktop_menu_open = False
        expects_text_document = any(
            step.get("semantic") == "shell_create_text_document" for step in self.steps
        )
        desktop_before = _desktop_text_files() if expects_text_document else {}
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
                done = i + 1
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
            step_method = ""
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
            if step.get("action") in ("click", "double_click", "right_click") and step.get("locator"):
                if not self._check_locator_retry(step):
                    locator_failed = True
                    if self._try_correction(i):
                        step_ok = self._last_correction_success
                        step_detail = "纠错库命中" if step_ok else "纠错动作失败"
                        step_method = "approved_correction"
                        if not step_ok:
                            self._pause_reasons.append({
                                "step": i, "reason": "已批准纠错动作执行失败"
                            })
                            self._pause_for_human(i, step_detail)
                            if self._stopped:
                                break
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
                                    step_method = retry_result.method
                                    step_detail = f"重试成功 ({retry_result.method})"
                            if self._stopped:
                                break
            if step_ok and not locator_failed and not step_already_executed:
                if not self._try_correction(i):
                    result = self._play_step_with_retry(step)
                    step_method = result.method
                    # A dispatched action is not successful merely because it
                    # returned.  Propagate the executor result so a failed
                    # input/window switch cannot be reported as a completed
                    # run or silently allow the next step to continue.
                    step_ok = bool(result.ok)
                    if not step_ok:
                        step_detail = result.reason or "执行失败"
                        self._pause_reasons.append({
                            "step": i,
                            "reason": f"执行失败: {action} ({step_detail})",
                        })
                        self._pause_for_human(i, step_detail)
                else:
                    step_ok = self._last_correction_success
                    step_detail = "纠错库命中" if step_ok else "纠错动作失败"
                    step_method = "approved_correction"
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
                "method": step_method,
                "effect": step.get("effect", "readonly"),
                "window": step.get("window", ""),
                "evidence": {
                    "locator_types": [
                        str(loc.get("type")) for loc in step.get("locator", [])
                        if loc.get("type")
                    ],
                },
                "ts": time.strftime("%H:%M:%S")
            })
            done = i + 1
            self._save_checkpoint(i + 1)
            prev_t = step.get("t", 0)
            if not self._sleep_interruptible(step_delay):
                break
        interrupted = self._stopped
        artifacts = []
        artifact_ok = True
        if expects_text_document and not interrupted:
            deadline = time.monotonic() + 3.0
            created = set()
            while time.monotonic() < deadline:
                now_files = _desktop_text_files()
                # 同名重建（Shell 兜底会先删同名残留再新建）不算"新增"文件名，
                # 以 mtime 变化识别本次回放实际写入的文件
                created = {p for p in now_files
                           if p not in desktop_before
                           or desktop_before[p] != p.stat().st_mtime_ns}
                if created:
                    break
                time.sleep(0.2)
            if created:
                artifacts = sorted(str(path) for path in created)
            else:
                artifact_ok = False
                self._step_results.append({
                    "step": done,
                    "action": "verify_artifact",
                    "text": "desktop *.txt",
                    "status": "failed",
                    "detail": "未检测到新建文本文件",
                    "method": "filesystem",
                    "effect": "readonly",
                    "window": "Program Manager",
                    "evidence": {"locator_types": []},
                    "ts": time.strftime("%H:%M:%S"),
                })
        success = (not interrupted and done >= len(self.steps) and artifact_ok
                   and all(item.get("status") == "ok" for item in self._step_results))
        summary = f"完成 {done}/{len(self.steps)} 步" + (" (已中断)" if interrupted else "")
        if not artifact_ok:
            summary += "；失败：未生成新的文本文件"
        try:
            if _com_inited:
                pythoncom.CoUninitialize()
        except Exception:
            pass
        side_effects = [
            {"step": item.get("step"), "effect": item.get("effect"),
             "action": item.get("action"), "window": item.get("window", "")}
            for item in self._step_results
            if item.get("effect") in {"mutating", "destructive"}
        ]
        return {
            "summary": summary,
            "steps": self._step_results,
            "pause_reasons": self._pause_reasons,
            "human_takeovers": list(self._pause_reasons),
            "side_effects": side_effects,
            "agent_suggestions": list(self._agent_suggestions),
            "artifacts": artifacts,
            "success": success,
        }

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
        点击类动作会自然消耗目标（菜单项点击后菜单关闭、关闭按钮点击后
        窗口销毁），目标消失是动作生效的正常表现，不算告警；只有非点击
        动作（如 input_text）目标消失才值得警告。
        uia 校验直接查控件存在性，不走带偏移/尺寸守卫的定位链——那些
        守卫是为"把点击落到正确坐标"设计的，用在存在性校验上会在窗口
        位置变化时误报。
        返回 True=验证通过 / False=warn / None=无可验证目标"""
        locs = step.get("locator", [])
        raw = (step.get("screen_x"), step.get("screen_y"))
        for loc in locs:
            if loc.get("type") == "uia" and loc.get("name"):
                from services.input.engine import _timed_call
                try:
                    ctrl = _timed_call(lambda: self._find_uia_ctrl(loc), timeout=2.0)
                except Exception:
                    ctrl = None
                if ctrl is not None:
                    return True
                if step.get("action") in ("click", "double_click", "right_click"):
                    return None  # 目标被点击消耗，属正常
                return False
            if loc.get("type") == "ocr" and loc.get("name"):
                x, y = self._try_locator(loc, raw)
                if x is None:
                    if step.get("action") in ("click", "double_click", "right_click"):
                        return None
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
            # Corrections are governed patches, not implicit memory.  A
            # rollback clears ``active_revision`` and must immediately stop a
            # future replay from applying the patch.
            if not bool(corr.get("approved", False)):
                continue
            if "active_revision" in corr and corr.get("active_revision") is None:
                continue
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
        self._last_correction_id = str(corr_id)
        log.info("  命中纠错 %s：回放人工纠正步骤 (%d 步)", corr_id, len(correct))
        success = True
        for c in correct:
            if self._stopped:
                success = False
                break
            try:
                result = self._play_step(c)
                if not result.ok:
                    success = False
                    break
            except Exception:
                success = False
                break
            if not self._sleep_interruptible(0.4):
                success = False
                break
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
        self._last_correction_success = bool(success)
        # A matched correction is considered handled even if its trial failed;
        # the caller records the failed governed patch and does not silently
        # execute the original action on top of it.
        return True

    def _try_llm_heal(self, idx, failed_step, error=""):
        """Ask the bounded agent for a *proposal* without executing it.

        R3 deliberately keeps model output outside the active plan.  The
        proposal is reported for later review/approval; the caller continues
        through the normal fail-closed human takeover path.
        """
        try:
            from services.llm_heal import get_healer
            healer = get_healer()
            if not healer.enabled:
                return None
            context = self.steps[max(0, idx-2):idx]
            log.info("  尝试 LLM 自愈 (步骤 %d)...", idx)
            result = healer.heal(failed_step, context, error)
            try:
                from services.computer_use_adapter import ComputerUseAdapter
                from paths import BASE_DIR
                evidence = BASE_DIR / "runs" / "screenshots" / "llm" / f"step_{idx:04d}.png"
                observed = ComputerUseAdapter().observe(evidence)
                visual = ComputerUseAdapter().plan(
                    f"Analyze the current screen and suggest the next safe action for this failed step: {failed_step}. Error: {error}", evidence)
                if visual:
                    result = {**(result or {}), "visual_plan": visual, "evidence": observed}
            except Exception as visual_error:
                log.debug("visual planning unavailable: %s", visual_error)
            if result and isinstance(result, dict):
                log.info("  LLM 自愈建议: %s", result.get("action", "?"))
                self._agent_suggestions.append({
                    "step": idx,
                    "error": str(error)[:200],
                    "proposal": result,
                    "status": "pending_approval",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                })
                # Never return a runnable step from the model path.
                return None
        except Exception as e:
            log.debug("LLM 自愈异常: %s", e)
        return None

    def _do_wait(self, step, prev_t):
        """等待步骤：等待目标窗口出现（阶段2.3），超时则继续"""
        try:
            duration = min(max(0.0, float(step.get("duration", 2.0))), WAIT_HARD_TIMEOUT)
        except (TypeError, ValueError):
            duration = 2.0
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

    # 虚拟桌面/壳层容器的类名：这些"父窗口"包含整个桌面，作为搜索根等于全桌面捞针
    _PSEUDO_HOST_CLASSES = {"#32769"}

    def _find_top_window(self, ua, loc):
        """按父窗口链解析候选顶级窗口根，返回控件列表（最内层真实窗口优先）。

        parents 链存在时找不到目标窗口就返回空列表（绝不 fallback 前台窗口乱搜，
        避免录制时后台窗口的同名控件被误命中）；只有完全没有 parents 信息才用
        前台窗口。注意两点：

        1. 链尾可能是"桌面 1"(#32769) 这类虚拟桌面容器，必须跳过；
        2. 弹出层控件（XAML 菜单/标题栏按钮）住 在独立的弹出窗口里，
           不在主窗口树下——所以要按 内层→外层 逐个窗口尝试，
           而不是只取最外层那一个根。
        """
        parents = loc.get("parents", [])
        roots = []
        if parents:
            for p in parents:  # 原始顺序 = 最内层优先
                p_name = p.get("name", "").strip()
                p_class = p.get("class", "").strip()
                if p_class in self._PSEUDO_HOST_CLASSES:
                    continue
                if p_name.startswith("桌面 ") and p_class == "":
                    continue
                hwnd = None
                by_name = False
                if p_name:
                    hwnd = _find_window_by_title(p_name)
                    by_name = hwnd is not None
                if hwnd is None and p_class:
                    hwnd = win32gui.FindWindow(p_class, None)
                if hwnd:
                    ctrl = ua.ControlFromHandle(hwnd)
                    if ctrl is not None:
                        roots.append((by_name, ctrl))
            # 按标题解析的窗口无歧义，优先搜索；仅类名解析的窗口可能命中
            # 其它应用的同类 XAML 岛，放后面，避免在错误根上耗尽定位超时
            roots.sort(key=lambda pair: 0 if pair[0] else 1, reverse=False)
            return [ctrl for _, ctrl in roots]
        fg = win32gui.GetForegroundWindow()
        ctrl = ua.ControlFromHandle(fg)
        return [ctrl] if ctrl is not None else []

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

            # 组合搜索：最严格的全属性组合优先，其次名称+类型、自动化ID。
            # 不做裸类名匹配——同窗口内同类控件遍地（如所有 Button），
            # 会命中树里第一个同类的无关控件并误触发
            candidates = []
            strict = {}
            if ctrl_type is not None:
                strict["ControlType"] = ctrl_type
            if target_name:
                strict["Name"] = target_name
            if target_class:
                strict["ClassName"] = target_class
            if target_auto_id:
                strict["AutomationId"] = target_auto_id
            if len(strict) >= 2:
                candidates.append(strict)
            kw = {}
            if ctrl_type is not None:
                kw["ControlType"] = ctrl_type
            if target_name:
                kw["Name"] = target_name
            if kw:
                candidates.append(kw)
            if target_auto_id:
                candidates.append(dict(AutomationId=target_auto_id))

            windows = self._find_top_window(ua, loc)
            if not windows:
                return None

            for window in windows:
                for kw in candidates:
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
            # 定位链逐级回退是设计行为，单级失败（含目标窗口重绘中的瞬态
            # COMError）降到 debug，避免每次回放刷屏
            log.debug("[UIA 定位失败] %s", e)
        return None

    def _locate_by_uia(self, loc, raw=(None, None)):
        """用 UI Automation 重新定位元素。多属性组合搜索，避免逐类型穷举。
        目标窗口重绘瞬间（如标题栏随内容改名）COM 查询会抛瞬态错误，
        短间隔重试可显著降低定位率下降导致的坐标回退。"""
        from services.input.engine import _timed_call
        ctrl = None
        for attempt in range(3):
            try:
                ctrl = _timed_call(lambda: self._find_uia_ctrl(loc), timeout=2.5)
            except Exception:
                ctrl = None
            if ctrl is not None:
                break
            if attempt < 2:
                time.sleep(0.3)
        if ctrl is None:
            return None, None
        try:
            def _point(ctrl):
                rect = ctrl.BoundingRectangle
                # 容器尺寸阈值检查：面积超过窗口 50% 时跳过（避免找到过大容器如"桌面 1"）。
                # 内容型控件（编辑器/文档）命中即合法目标——文本编辑器本来就该占满窗口
                try:
                    typ = str(ctrl.ControlTypeName or "")
                except Exception:
                    typ = ""
                if typ not in ("DocumentControl", "EditControl") \
                        and (rect.right - rect.left) * (rect.bottom - rect.top) > 0:
                    ctrl_area = (rect.right - rect.left) * (rect.bottom - rect.top)
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
                # 偏移守卫只对弱身份定位器（无名称/自动化ID的结构匹配）生效：
                # 那种匹配可能命中错误元素。带名称的定位已经锁定了窗口与控件
                # 身份——桌面图标重排、窗口/对话框移动造成的坐标偏移是正常
                # 现象，拒绝它只会退回更不可靠的录制原始坐标
                if res is None or res[0] is None or res[1] is None:
                    return None
                if not (loc.get("name") or loc.get("auto_id")):
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
            log.debug("[UIA 定位失败] %s", e)
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
                    # 最小化窗口的 WindowRect 是 (-32000,-32000) 附近的占位值，
                    # 按它换算出来的坐标必然无效，跳过让后续定位器接管
                    if win32gui.IsIconic(hwnd):
                        return None, None
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
        """判断是否为桌面级操作（右键菜单、桌面点击等）。
        注意：'主机弹出窗口' 既是 Win11 桌面右键菜单的宿主标题，也是应用自身
        XAML 弹出层（如记事本未保存提示）的标题。只有桌面菜单上下文存活
        （_desktop_menu_open，说明刚在桌面上开了右键菜单）时才按桌面动作处理；
        否则必须走普通 UIA/坐标链路——桌面分支的 _show_desktop() 会把应用
        弹出层直接冲掉，raw 坐标也会点空。"""
        window = step.get("window", "")
        if window == "主机弹出窗口":
            return bool(self._desktop_menu_open)
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
                # invoke 成功后目标元素可能立即销毁（如点击关闭对话框），
                # 再读 Name/ControlTypeName 会抛 COMError，绝不能让它炸掉回放线程
                try:
                    desc = f"{ctrl.Name!r} ({getattr(ctrl, 'ControlTypeName', '?')})"
                except Exception:
                    desc = "<元素已销毁>"
                log.info("  -> UIA %s 成功: %s", action_name, desc)
                return self._ok(f"uia_{action_name}")
            return None
        return None

    def _activate_target(self, hwnd):
        """确保目标窗口在前台（点击/输入前激活，避免动作落在被遮挡窗口上）"""
        try:
            if hwnd and win32gui.IsWindow(hwnd):
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE：还原最小化窗口
                    time.sleep(0.3)
                fg = win32gui.GetForegroundWindow()
                if fg != hwnd:
                    _force_foreground(hwnd)
        except Exception as e:
            log.debug("激活目标窗口失败: %s", e)

    @staticmethod
    def _is_idempotent_missing_window(step):
        """Recognize a close-button click whose target is already closed.

        Replaying a desktop recording often starts after the recorded app has
        already been closed.  Only a click in the recorded top-right title-bar
        region is safe to treat as an already-satisfied action; other missing
        windows still require human takeover.
        """
        if step.get("action") not in {"click", "double_click"}:
            return False
        if not str(step.get("window", "")).strip():
            return False
        for loc in step.get("locator", []):
            try:
                x = float(loc.get("client_pct_x"))
                y = float(loc.get("client_pct_y"))
            except (TypeError, ValueError):
                continue
            if x >= 88.0 and 0.0 <= y <= 8.0:
                return True
        return False

    def _do_click(self, step):
        # Shell desktop/menu coordinates are physical targets.  A generic
        # PaneControl UIA locator (often the desktop root) can report Invoke
        # success without selecting the menu item, so never let it short
        # circuit desktop clicks.
        if self._desktop_menu_open:
            x0, y0 = self._resolve_coords(step)
            menu_name = next((str(loc.get("name", "")).strip()
                              for loc in step.get("locator", [])
                              if loc.get("type") == "uia"
                              and "MenuItem" in str(loc.get("control_type", ""))
                              and str(loc.get("name", "")).strip()), "")
            if step.get("semantic") == "shell_create_text_document":
                menu_name = "文本文档" if not menu_name else menu_name
                # 直接标签查找失败时，目标项多半还在折叠的"新建"子菜单里：
                # 先展开父级再重查，否则会走到坐标兜底误点其它菜单项
                if menu_name and not self._invoke_shell_menu_label((menu_name,), prefer_physical=True):
                    if self._expand_shell_submenu(("新建", "new")):
                        time.sleep(0.6)
                if menu_name and self._invoke_shell_menu_label((menu_name,), prefer_physical=True):
                    time.sleep(0.25)
                    self._desktop_menu_open = self._shell_menu_visible()
                    log.info("  -> 菜单标签命中 %r", menu_name)
                    return self._ok("uia_shell_menu_label")
                expected = (menu_name,) if menu_name else None
                if x0 is not None and y0 is not None and \
                        self._invoke_shell_menu_at(x0, y0, prefer_physical=True, expected=expected):
                    time.sleep(0.25)
                    self._desktop_menu_open = self._shell_menu_visible()
                    log.info("  -> 菜单点按坐标命中 (%d,%d)", x0, y0)
                    return self._ok("uia_shell_menu_point")
                # UI 菜单路径全部失败：该语义的结果可确定性达成（桌面出现新 txt），
                # 直接 Shell 新建等价文件，比盲点菜单坐标可靠得多。
                # 文件名用后续步骤引用的录制名（编号与桌面残留无关，重放幂等）
                recorded_base = self._recorded_text_doc_name(step.get("index", -1))
                if self._create_desktop_text_document(recorded_base):
                    self._close_shell_menu()
                    self._desktop_menu_open = False
                    # 等新文件的桌面图标在 UIA 树中物化（Explorer 刷新有延迟），
                    # 后续步骤会按名称定位该图标，过早执行会退回陈旧的原始坐标
                    wait_name = recorded_base
                    deadline = time.time() + 6.0
                    while wait_name and time.time() < deadline and not self._stopped:
                        if self._desktop_icon_visible(wait_name):
                            log.info("  -> 新建文件图标已可见")
                            break
                        time.sleep(0.5)
                    return self._ok("shell_create_text_document_fallback")
            elif menu_name and self._invoke_shell_menu_label((menu_name,), prefer_physical=True):
                time.sleep(0.25)
                self._desktop_menu_open = self._shell_menu_visible()
                log.info("  -> 菜单标签命中 %r", menu_name)
                return self._ok("uia_shell_menu_label")
            expected = (menu_name,) if menu_name else None
            if x0 is not None and y0 is not None and \
                    self._invoke_shell_menu_at(x0, y0, prefer_physical=True, expected=expected):
                time.sleep(0.25)
                self._desktop_menu_open = self._shell_menu_visible()
                log.info("  -> 菜单点按坐标命中 (%d,%d)", x0, y0)
                return self._ok("uia_shell_menu_point")
            # The previous leaf selection may already have closed the menu;
            # continue with the ordinary desktop target instead of treating a
            # subsequent file click as a missing menu item.
            if not self._shell_menu_visible():
                self._desktop_menu_open = False
        if not self._is_desktop_action(step):
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
        # 非桌面动作：先定位并激活目标窗口（含最小化还原），再解析坐标，
        # 这样窗口内百分比定位器才能基于还原后的窗口矩形换算
        hwnd = None
        if not self._is_desktop_action(step):
            hwnd = self._target_hwnd(step.get("window", ""), step.get("hwnd"))
            if hwnd:
                self._activate_target(hwnd)
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return self._fail("click", "no_coords")
        if self._is_desktop_action(step):
            log.info("  -> 桌面点击 (%d,%d)", x, y)
            if not self._desktop_menu_open:
                _show_desktop()
            if not _desktop_window_at(x, y, allow_menu=self._desktop_menu_open):
                return self._fail("click", "desktop_not_target")
            result = self._engine.click_desktop(x, y)
            # A desktop context-menu click may open a nested submenu (for
            # example Desktop -> New -> Text Document). Keep the menu context
            # alive across that click so the next physical click can target
            # the popup's ``#32768`` window. The next desktop right-click
            # starts a fresh context; non-menu targets are still validated by
            # ``_desktop_window_at``.
            return result
        if not hwnd:
            if self._is_idempotent_missing_window(step):
                log.info("  -> skip already-closed window: %r", step.get("window", ""))
                return self._ok("skip_missing_window")
            return self._fail("click", "no_window")
        log.info("  -> 点击 (%d,%d) -> %r", x, y, step.get("window", ""))
        return self._engine.click(hwnd, x, y)

    def _shell_menu_roots(self):
        """收集当前可见的 Shell 菜单根（Win32 #32768 与 Win11 XAML 弹出层）"""
        import uiautomation as ua
        roots = []
        desktop = ua.GetRootControl()
        for child in desktop.GetChildren():
            try:
                cls = str(child.ClassName or "")
                typ = str(child.ControlTypeName or "")
                if (typ == "MenuControl" or cls == "#32768" or "Xaml" in cls
                        or "PopupWindowSiteBridge" in cls):
                    roots.append(child)
            except Exception:
                continue
        try:
            popup = ua.WindowControl(ClassName="#32768")
            if popup.Exists(0.2):
                roots.append(popup)
        except Exception:
            pass
        return roots

    @staticmethod
    def _desktop_icon_visible(name):
        """桌面列表里是否存在指定名称的图标（Progman/WorkerW 均查）"""
        try:
            import uiautomation as ua
            for cls in ("Progman", "WorkerW"):
                h = win32gui.FindWindow(cls, None)
                if not h:
                    continue
                root = ua.ControlFromHandle(h)
                if root is None:
                    continue
                item = root.ListItemControl(Name=name, searchDepth=8)
                if item.Exists(0.5):
                    return True
        except Exception:
            pass
        return False

    def _recorded_text_doc_name(self, after_index):
        """从后续步骤中提取录制时新建文本文件的名字（如 '新建 文本文档 (3)'）。
        桌面右键新建的文件名取决于当时桌面已有多少同类文件，重放环境的残留
        文件会让 Explorer 编号漂移；按录制引用名精确创建才能保证后续步骤
        （双击图标/切换记事本窗口标题）全部对得上。"""
        import re
        pat = re.compile(r"新建 文本文档(?: \(\d+\))?")
        for s in self.steps:
            if s.get("index", -1) <= after_index:
                continue
            m = pat.search(str(s.get("window", "")))
            if m:
                return m.group(0)
            for loc in s.get("locator", []):
                if loc.get("type") == "uia":
                    m = pat.search(str(loc.get("name", "")))
                    if m:
                        return m.group(0)
        return None

    def _create_desktop_text_document(self, target_base=None):
        """在桌面新建文本文档。target_base 提供时精确使用该名字（同名旧文件
        先删除，保证重放幂等）；否则按 Explorer 规则取下一个空位编号。"""
        try:
            from win32com.shell import shell, shellcon
            desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOP, None, 0))
        except Exception:
            desktop = Path.home() / "Desktop"
        if target_base:
            base = target_base[:-4] if target_base.endswith(".txt") else target_base
            target = desktop / f"{base}.txt"
        else:
            base_path = desktop / "新建 文本文档.txt"
            stem, suffix = base_path.stem, base_path.suffix
            target, n = base_path, 2
            while target.exists():
                target = desktop / f"{stem} ({n}){suffix}"
                n += 1
        try:
            if target.exists():
                target.unlink()  # 清掉上次回放的残留，保证后续步骤引用的就是本次新建
            target.write_text("", encoding="utf-8")
            # 通知 Shell 刷新，桌面图标尽快出现（后续步骤会按名称定位）
            try:
                import ctypes
                ctypes.windll.shell32.SHChangeNotify(0x80000000, 0x1000,
                                                     str(target), None)
            except Exception:
                pass
            log.info("  -> Shell 新建文本文档: %s", target.name)
            return True
        except Exception as e:
            log.info("  Shell 新建文本文档失败: %s", e)
            return False

    def _close_shell_menu(self):
        """发送 ESC 关闭可能残留的桌面右键菜单"""
        try:
            import ctypes
            VK_ESCAPE = 0x1B
            ctypes.windll.user32.keybd_event(VK_ESCAPE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_ESCAPE, 0, 2, 0)  # KEYEVENTF_KEYUP
            time.sleep(0.3)
        except Exception:
            pass

    def _expand_shell_submenu(self, parent_labels):
        """找到可见 Shell 菜单中匹配的父级 MenuItem（如"新建"）并展开其子菜单。
        优先 ExpandCollapsePattern；XAML 菜单不吃 pattern 时物理悬停触发。"""
        try:
            import uiautomation as ua
            labels = tuple(str(x).casefold() for x in parent_labels)
            queue = list(self._shell_menu_roots())
            visited = 0
            while queue and visited < 2000:
                control = queue.pop(0)
                visited += 1
                try:
                    name = str(control.Name or "").strip().casefold()
                    typ = str(control.ControlTypeName or "")
                    if typ == "MenuItemControl" and any(l in name for l in labels):
                        try:
                            pat = control.GetPattern(ua.PatternId.ExpandCollapsePattern)
                            if pat is not None:
                                pat.Expand()
                                return True
                        except Exception:
                            pass
                        # 物理悬停：把鼠标移到菜单项上让 XAML 子菜单弹出
                        rect = control.BoundingRectangle
                        if rect.right > rect.left and rect.bottom > rect.top:
                            x = int((rect.left + rect.right) / 2)
                            y = int((rect.top + rect.bottom) / 2)
                            ctypes.windll.user32.SetCursorPos(x, y)
                            return True
                    queue.extend(control.GetChildren())
                except Exception:
                    continue
        except Exception as exc:
            log.debug("展开子菜单失败: %s", exc)
        return False

    def _invoke_shell_menu_at(self, x, y, prefer_physical=False, expected=None):
        """Invoke the live menu item at a recorded point.

        Prefer ControlFromPoint so XAML menu descendants with Handle=0 are
        still reachable.  Walk parents because Windows may return a text/icon
        child rather than the MenuItem itself.  ``expected`` 提供时校验命中的
        菜单项名称：坐标处是别的菜单项（如"新建"父级行）时拒绝盲点，
        避免假成功掩盖"目标在折叠子菜单里"的事实。
        """
        try:
            import uiautomation as ua
            from services.input.engine import _timed_call

            control = _timed_call(
                lambda: ua.ControlFromPoint(int(x), int(y)), timeout=1.5)
            for _ in range(8):
                if control is None:
                    break
                try:
                    if control.ControlTypeName == "MenuItemControl":
                        if expected:
                            name = str(control.Name or "").strip().casefold()
                            want = tuple(str(e).casefold() for e in expected if e)
                            if want and not any(e in name for e in want):
                                log.info("  菜单坐标命中 %r 与目标 %r 不符，拒绝盲点",
                                         control.Name, expected)
                                return False
                        return self._activate_shell_menu_control(control, prefer_physical)
                    control = control.GetParentControl()
                except Exception:
                    break
        except Exception as exc:
            log.debug("Shell menu point lookup failed: %s", exc)
            return False
        return False

    def _activate_shell_menu_control(self, control, prefer_physical=False):
        if prefer_physical:
            try:
                rect = control.BoundingRectangle
                x = int((rect.left + rect.right) / 2)
                y = int((rect.top + rect.bottom) / 2)
                if rect.right > rect.left and rect.bottom > rect.top:
                    self._engine.fg._mouse_click(x, y)
                    return True
            except Exception:
                pass
        try:
            if self._engine.invoke_ctrl(control):
                return True
        except Exception:
            pass
        try:
            rect = control.BoundingRectangle
            x = int((rect.left + rect.right) / 2)
            y = int((rect.top + rect.bottom) / 2)
            if rect.right > rect.left and rect.bottom > rect.top:
                self._engine.fg._mouse_click(x, y)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _shell_menu_visible():
        visible = False

        def visit(hwnd, _):
            nonlocal visible
            try:
                cls = win32gui.GetClassName(hwnd)
                if win32gui.IsWindowVisible(hwnd) and (
                        cls == "#32768" or "XamlExplorerHostIslandWindow" in cls
                        or "PopupWindowSiteBridge" in cls):
                    visible = True
            except Exception:
                pass
            return not visible

        try:
            win32gui.EnumWindows(visit, None)
        except Exception:
            pass
        return visible

    def _invoke_shell_menu_label(self, labels, prefer_physical=False):
        """Find and invoke a visible Windows Shell menu item by its label.

        Windows 11 menus are transient XAML/UIA trees and their coordinates
        move with DPI, taskbar position, and submenu placement.  Enumerate
        only the current popup/menu roots and recurse through their children;
        this avoids accidentally invoking a similarly named item in another
        application.
        """
        try:
            import uiautomation as ua
            labels = tuple(str(x).casefold() for x in labels)
            queue = list(self._shell_menu_roots())
            visited = 0
            while queue and visited < 2000:
                control = queue.pop(0)
                visited += 1
                try:
                    name = str(control.Name or "").strip().casefold()
                    typ = str(control.ControlTypeName or "")
                    if typ == "MenuItemControl" and any(label in name for label in labels):
                        if self._activate_shell_menu_control(control, prefer_physical):
                            return True
                    queue.extend(control.GetChildren())
                except Exception:
                    continue
        except Exception as exc:
            log.debug("Shell menu dynamic lookup failed: %s", exc)
        return False

    def _do_double_click(self, step):
        # 双击不走 pattern 链：桌面 ListItem 的 Invoke 会抛 COMError 且 Select 只是选中，
        # "假装成功"会掩盖未打开文件的失败。物理双击（UIA 定位坐标）最忠实可靠。
        # 先定位并激活目标窗口（含最小化还原），再解析坐标
        hwnd = None
        if not self._is_desktop_action(step):
            hwnd = self._target_hwnd(step.get("window", ""), step.get("hwnd"))
            if hwnd:
                self._activate_target(hwnd)
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return self._fail("double_click", "no_coords")
        if self._is_desktop_action(step):
            log.info("  -> 桌面双击 (%d,%d)", x, y)
            if not self._desktop_menu_open:
                _show_desktop()
            if not _desktop_window_at(x, y, allow_menu=self._desktop_menu_open):
                return self._fail("double_click", "desktop_not_target")
            result = self._engine.double_click_desktop(x, y)
            # Preserve an open context-menu/submenu for the same reason as
            # ``_do_click`` above.
            return result
        if not hwnd:
            if self._is_idempotent_missing_window(step):
                log.info("  -> skip already-closed window: %r", step.get("window", ""))
                return self._ok("skip_missing_window")
            return self._fail("double_click", "no_window")
        log.info("  -> 双击 (%d,%d) -> %r", x, y, step.get("window", ""))
        return self._engine.double_click(hwnd, x, y)

    def _do_right_click(self, step):
        # 同 _do_double_click：先激活目标窗口再解析坐标
        hwnd = None
        if not self._is_desktop_action(step):
            hwnd = self._target_hwnd(step.get("window", ""), step.get("hwnd"))
            if hwnd:
                self._activate_target(hwnd)
        x, y = self._resolve_coords(step)
        if x is None or y is None:
            return self._fail("right_click", "no_coords")
        if self._is_desktop_action(step):
            log.info("  -> 桌面右键 (%d,%d)", x, y)
            _show_desktop()
            if not _desktop_window_at(x, y):
                return self._fail("right_click", "desktop_not_target")
            self._engine.fg._mouse_right_click(x, y)
            self._desktop_menu_open = True
            time.sleep(0.5)
            return self._ok("desktop_right_click")
        if not hwnd:
            return self._fail("right_click", "no_window")
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
        # 拼音上屏模式（纯字母+结尾空格，如 'tielu ' -> 铁路）：
        # 切换步的指示器读数可能因前台竞态失真，键入前一刻目标窗口
        # 已被激活，此时读取/校正输入法模式最可靠
        stripped = text.rstrip(" ")
        if text.endswith(" ") and stripped and stripped.isascii() and stripped.isalpha():
            try:
                self._activate_target(hwnd)
                time.sleep(0.2)
                if self._engine.ime_mode_english() is True:
                    self._engine.ime_toggle(hwnd, to_english=False)
                    log.info("  -> 键入前 IME 校正为中文模式")
            except Exception as e:
                log.debug("IME 键入前校正失败: %s", e)
        if step.get("window") == "Program Manager" and any(
                s.get("semantic") == "shell_create_text_document" for s in self.steps):
            # After Shell creates a document the rename edit is a transient
            # UIA control, not the desktop root.  Resolve it by focus before
            # sending any text; never type into Program Manager itself.
            try:
                import uiautomation as ua
                for _ in range(20):
                    focused = ua.GetFocusedControl()
                    if focused and focused.ControlTypeName in ("EditControl", "DocumentControl"):
                        pattern = focused.GetValuePattern()
                        if pattern:
                            pattern.SetValue(text)
                            return self._ok("uia_shell_rename")
                    time.sleep(0.15)
                # Shell may create the item without entering rename mode when
                # Invoke is used. Locate the newly-created desktop item and
                # explicitly enter F2 rename mode before setting its value.
                root = ua.ControlFromHandle(hwnd) if hwnd else ua.GetRootControl()
                candidate = None
                queue = [root] if root else []
                while queue and not candidate:
                    control = queue.pop(0)
                    try:
                        name = str(control.Name or "").casefold()
                        is_text_item = control.ControlTypeName == "ListItemControl" and any(
                                key in name for key in ("文本文档", "文本文件", "text document", "new text"))
                        if not is_text_item and control.ControlTypeName == "ListItemControl":
                            try:
                                current = _desktop_text_files()
                                is_text_item = bool(set(current) - set(desktop_before))
                            except Exception:
                                pass
                        if is_text_item:
                            candidate = control
                            break
                        queue.extend(control.GetChildren())
                    except Exception:
                        continue
                if candidate:
                    candidate.SetFocus()
                    rect = candidate.BoundingRectangle
                    self._engine.fg._mouse_click(
                        int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2))
                    self._engine.fg.key_combo(hwnd, [0x71])  # F2
                    for _ in range(12):
                        time.sleep(0.15)
                        focused = ua.GetFocusedControl()
                        if focused and focused.ControlTypeName in ("EditControl", "DocumentControl"):
                            pattern = focused.GetValuePattern()
                            if pattern:
                                pattern.SetValue(text)
                                return self._ok("uia_shell_rename_f2")
            except Exception:
                pass
            # Windows Explorer may begin rename only after its shell view has
            # returned focus.  As the last deterministic fallback, type into
            # the active shell rename field via foreground input; no generic
            # top-level-window injection is allowed.
            try:
                fg = win32gui.GetForegroundWindow()
                if fg and _same_window_kind(fg, "Program Manager"):
                    self._engine.fg.type_text(fg, text)
                    return self._ok("shell_foreground_rename")
            except Exception:
                pass
            return self._fail("input_text", "shell_rename_control_not_found")
        if not hwnd:
            return self._fail("input_text", "no_window")
        self._activate_target(hwnd)
        log.info("  输入: %r -> %r", text[:50], win32gui.GetWindowText(hwnd)[:40])
        # Replace the captured field value so replay remains repeatable when
        # the target contains data from an earlier run.
        if step.get("replace", True):
            # Native editors (including modern Notepad) may expose no usable
            # UIA ValuePattern.  Set the actual child edit control directly
            # when available; this makes replacement deterministic across
            # repeated runs.
            try:
                import win32con
                children = []
                win32gui.EnumChildWindows(hwnd, lambda h, _: children.append(h), None)
                for child in children:
                    cls = win32gui.GetClassName(child).lower()
                    if "edit" in cls or "richedit" in cls:
                        win32gui.SendMessage(child, win32con.WM_SETTEXT, 0, text)
                        return self._ok("win32_settext")
            except Exception:
                pass
            self._uia_click_edit(hwnd)
            try:
                import uiautomation as ua
                focused = ua.GetFocusedControl()
                candidates = [focused] if focused else []
                root = ua.ControlFromHandle(hwnd)
                if root:
                    for kind in ("DocumentControl", "EditControl"):
                        try:
                            candidates.append(getattr(root, kind)(searchDepth=32))
                        except Exception:
                            pass
                for control in candidates:
                    if control and control.Exists(0.2):
                        pattern = control.GetValuePattern()
                        if pattern:
                            pattern.SetValue(text)
                            return self._ok("uia_value_replace")
            except Exception:
                pass
            selected = self._engine.fg.key_combo(hwnd, [0x11, 0x41])
            if not selected.ok:
                return selected
            # Foreground typing targets the focused child control, unlike
            # message-based replacement which can target the top-level hwnd.
            try:
                import win32clipboard
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                pasted = self._engine.fg.key_combo(hwnd, [0x11, 0x56])
                if pasted.ok:
                    return pasted
            except Exception:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
            return self._engine.fg.type_text(hwnd, text)
        # 录制流程含 IME 切换时，全部文本都走真实键入：
        # - SetValue/PostMessage 通道绕过输入法，拼音串只会是字面量
        # - 数字/ASCII 与拼音混排时若通道不一致，段落会散落在不同元素里丢失
        ime_flow = any(str(s.get("key", "")).strip().lower()
                       in self._engine._IME_TOGGLE_COMBOS for s in self.steps)
        return self._engine.type_text(hwnd, text, keystroke_only=ime_flow or
                                      (text.endswith(" ") and text.rstrip(" ").isascii()
                                       and text.rstrip(" ").isalpha()))

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
        # IME 切换键是翻转语义，回放起点的输入法状态不可控：
        # 按下一步文本的意图决定目标模式，仅在与当前不符时才切换
        if key.strip().lower() in self._engine._IME_TOGGLE_COMBOS:
            to_english = not self._next_step_wants_chinese(step)
            log.info("  IME 切换 -> %s模式", "英" if to_english else "中")
            return self._engine.ime_toggle(hwnd, to_english)
        return self._engine.key_press(hwnd, key)

    def _next_step_wants_chinese(self, step) -> bool:
        """录制里 ctrl+space 的意图几乎都是"切到中文输拼音"。
        判定：下一个 input_text 的文本含 CJK，或是"纯字母+结尾空格"
        （拼音串以空格上屏，如 'tielu ' -> 铁路）。"""
        idx = step.get("index", -1)
        for s in self.steps:
            if s.get("index", -1) <= idx:
                continue
            if s.get("action") != "input_text":
                if s.get("action") in ("wait", "switch_window"):
                    continue
                break
            text = str(s.get("text", ""))
            if any('\u4e00' <= ch <= '\u9fff' for ch in text):
                return True
            letters = text.rstrip(" ")
            if letters.isascii() and letters.isalpha() and text.endswith(" "):
                return True
            return False
        return False

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

def _desktop_text_files():
    """Return current user desktop text files using the Shell folder path."""
    try:
        from win32com.shell import shell, shellcon
        desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOP, None, 0))
    except Exception:
        import os
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    try:
        return {path.resolve(): path.stat().st_mtime_ns for path in desktop.iterdir()
                if path.is_file() and path.suffix.casefold() == ".txt"}
    except Exception:
        return {}

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


def _desktop_window_at(x, y, allow_menu=False):
    """Check that a physical desktop action is aimed at the shell.

    ``mouse_event`` reports success even when no interactive desktop owns the
    coordinates.  Verify the HWND under the point before treating a desktop
    click as completed; a context-menu HWND is valid only for the click that
    follows a recorded right-click.
    """
    try:
        hwnd = win32gui.WindowFromPoint((int(x), int(y)))
        if not hwnd:
            return False
            shell = ctypes.windll.user32.GetShellWindow()
            return bool(shell and win32gui.IsWindowVisible(shell))
        classes = set()
        current = hwnd
        for _ in range(5):
            if not current:
                break
            try:
                classes.add(win32gui.GetClassName(current))
                parent = win32gui.GetParent(current)
            except Exception:
                break
            if not parent or parent == current:
                break
            current = parent
        # The Windows taskbar is part of the shell surface too.  A common
        # recorded flow right-clicks the desktop, chooses ``New -> Text
        # Document``, then clicks a taskbar button to return to the recorder
        # window.  That final click is still recorded with ``Program
        # Manager`` as the foreground title, but WindowFromPoint resolves to
        # ``Shell_TrayWnd`` (or one of its task-list children).  Accept only
        # these explicit shell classes; never treat an arbitrary foreground
        # window as a desktop target.
        desktop_classes = {
            "Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32",
            "Shell_TrayWnd", "MSTaskSwWClass", "MSTaskListWClass",
            "TaskListThumbnailWnd", "Windows.UI.Core.CoreWindow",
        }
        if classes & desktop_classes:
            return True
        menu_classes = {
            "#32768", "InputSiteWindowClass",
            "Microsoft.UI.Content.DesktopChildSiteBridge",
            "XamlExplorerHostIslandWindow_WASDK",
            "Microsoft.UI.Content.PopupWindowSiteBridge",
        }
        return allow_menu and bool(classes & menu_classes)
    except Exception:
        return False


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
    # Multiple same-class windows are resolved by z-order below.
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
