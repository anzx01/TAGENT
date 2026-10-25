"""
录制引擎 - 基于 ScreenClaw 的钩子 + 状态机事件分类器
将原始钩子事件归纳为操作步骤：click / right_click / input_text / key_press / scroll
每步截图 + 百分比坐标 + 窗口信息
"""
import json
import threading
import time
from pathlib import Path

import win32gui

from hook import HookManager, LBDOWN, LBUP, RBDOWN, RBUP, WHEEL, KDOWN, KUP
from capture import capture_window, capture_screen, GridRenderer
from logger import get_logger
from services.pii_guard import mask_pii

log = get_logger("recorder")

# 分类阈值
CLICK_DIST = 5           # 像素
CLICK_TIME = 300_000     # 300ms
SCROLL_MERGE = 500_000   # 500ms
TEXT_TIMEOUT = 2_000_000  # 2s（避免长文本被拆分）
SWIPE_TIME = 300_000     # 拖拽判定阈值，与 CLICK_TIME 一致
UIA_GRAB_TIMEOUT = 0.8   # Shell 菜单异步出现时仍要捕获菜单项证据
WAIT_THRESHOLD = 3.0      # 步骤间隔超过 3s 生成等待步骤（阶段2.3）

MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
FUNC_VKS = set(range(0x70, 0x88)) | {0x08, 0x09, 0x0D, 0x1B, 0x2E, 0x23, 0x24, 0x21, 0x22,
                                      0x25, 0x26, 0x27, 0x28}

VK_NAMES = {
    0x0D: "enter", 0x09: "tab", 0x1B: "esc", 0x20: "space",
    0x08: "backspace", 0x2E: "delete", 0x24: "home", 0x23: "end",
    0x21: "pageup", 0x22: "pagedown",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x11: "ctrl", 0x12: "alt", 0x10: "shift", 0x5B: "win",
    # 扩展键码（左/右）
    0xA0: "shift", 0xA1: "shift", 0xA2: "ctrl", 0xA3: "ctrl",
    0xA4: "alt", 0xA5: "alt", 0x5C: "win",
}
for i in range(12):
    VK_NAMES[0x70 + i] = f"f{i+1}"

VK_SYMBOLS = {
    0xBA: ';', 0xBB: '=', 0xBC: ',', 0xBD: '-', 0xBE: '.', 0xBF: '/',
    0xC0: '`', 0xDB: '[', 0xDC: '\\', 0xDD: ']', 0xDE: "'",
}
VK_SHIFT_SYMBOLS = {
    0x30: ')', 0x31: '!', 0x32: '@', 0x33: '#', 0x34: '$',
    0x35: '%', 0x36: '^', 0x37: '&', 0x38: '*', 0x39: '(',
    0xBA: ':', 0xBB: '+', 0xBC: '<', 0xBD: '_', 0xBE: '>',
    0xBF: '?', 0xC0: '~', 0xDB: '{', 0xDC: '|', 0xDD: '}', 0xDE: '"',
}


def vk_to_char(vk: int, shift: bool) -> str:
    """VK 码转可打印字符"""
    if 0x41 <= vk <= 0x5A:
        return chr(vk) if shift else chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return VK_SHIFT_SYMBOLS.get(vk, '') if shift else chr(vk)
    if vk == 0x20:
        return ' '
    if shift:
        return VK_SHIFT_SYMBOLS.get(vk, '')
    return VK_SYMBOLS.get(vk, '')


def vk_to_name(vk: int) -> str:
    return VK_NAMES.get(vk, chr(vk) if 0x20 <= vk < 0x7F else f"vk_{vk:#x}")


class UiaWorker:
    """专用 UIA 工作线程：CoInitialize 后常驻，请求/响应队列通信，带超时"""

    def __init__(self):
        import queue as _q
        self._req = _q.Queue()
        self._res = _q.Queue()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._threads = [threading.Thread(target=self._run, daemon=True)
                         for _ in range(2)]
        for thread in self._threads:
            thread.start()

    def _run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        while True:
            item = self._req.get()
            if item is None:
                break
            seq, x, y, response = item
            try:
                import uiautomation as ua
                result = self._grab(ua, x, y)
            except Exception:
                result = None
            response.put((seq, result))

    def _grab(self, ua, x, y):
        elem = None
        if x is not None and y is not None:
            try:
                # uiautomation.ControlFromPoint takes integer coordinates
                # directly (there is no public ``ua.Point`` type).
                elem = ua.ControlFromPoint(int(x), int(y))
            except Exception:
                pass
        if elem is None:
            elem = ua.GetFocusedControl()
        if elem is None:
            return None
        # XAML Shell menus expose the text glyph as the point element while
        # the actionable ancestor is MenuFlyoutItem/MenuItemControl. Promote
        # to that ancestor so compilation/replay can invoke the item by its
        # semantic label instead of recording a non-actionable TextBlock.
        try:
            parent = elem.GetParentControl()
            if parent and parent.ControlTypeName == "MenuItemControl":
                elem = parent
        except Exception:
            pass
        evidence = {
            "name": (elem.Name or "")[:200],
            "type": elem.ControlTypeName or "",
            "class": elem.ClassName or "",
            "auto_id": (elem.AutomationId or "")[:200],
        }
        path = []
        parent = elem.GetParentControl()
        depth = 0
        while parent and depth < 5:
            p_type = parent.ControlTypeName or ""
            p_class = parent.ClassName or ""
            p_name = (parent.Name or "")[:100]
            if p_type == "Window" or p_class:
                path.append({"type": p_type, "name": p_name, "class": p_class})
            parent = parent.GetParentControl()
            depth += 1
        if path:
            evidence["parents"] = path
        try:
            p = elem.GetParentControl()
            if p:
                sibs = p.GetChildren()
                for i, s in enumerate(sibs):
                    if s.Name == elem.Name and s.ControlTypeName == elem.ControlTypeName:
                        evidence["sibling_index"] = i
                        break
        except Exception:
            pass
        return evidence

    def grab(self, x, y, timeout=0.8):
        """请求 UIA 证据，超时返回 None。用序列号防止迟到结果污染。"""
        return self.resolve(self.submit(x, y), timeout)

    def submit(self, x, y):
        import queue as _q
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        response = _q.Queue(maxsize=1)
        self._req.put((seq, x, y, response))
        return seq, response

    @staticmethod
    def resolve(request, timeout=0.8):
        if not request:
            return None
        expected_seq, response = request
        try:
            seq, result = response.get(timeout=timeout)
            if seq != expected_seq:
                return None  # 迟到/错位结果
            return result
        except Exception:
            return None


class Recorder:
    _SELF_WINDOW_MARKERS = ("智能操作助手", "操作助手")

    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.shot_dir = self.output_dir / "screenshots"
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        self.hook = HookManager()
        self.steps = []
        self.recording = False
        self._stop_event = threading.Event()
        self._proc_thread = None
        self._shot_idx = 0
        self._shot_threads = []
        self._renderer = GridRenderer()
        self._uia_worker = None  # 录制启动时创建

        # 分类器状态
        self._pending_click = None
        self._pending_scroll = None
        self._pending_text = None
        self._held_mods = set()
        self._last_fg_hwnd = 0
        self._last_kdown_fg_hwnd = 0
        self._last_fg_title = ""
        self._event_seq = 0
        self._event_lock = threading.Lock()
        # Wall-clock anchor for deterministic event timestamps.  Hook ``t``
        # is monotonic relative to the recording start; retaining the anchor
        # lets diagnostics and later replays correlate actions with external
        # application logs without using the delayed append time.
        self._start_epoch = 0.0
        # 截图敏感遮挡（frame_redactor）状态：文本层检出的原始值 cross-feed 到帧
        self._known_secrets: set[str] = set()
        self._frame_stats = {"frames": 0, "redacted": 0, "regions": 0, "withheld": 0}
        self._ocr_status = "enabled"

    def start(self):
        self.steps = []
        self._shot_idx = 0
        self._known_secrets = set()
        self._frame_stats = {"frames": 0, "redacted": 0, "regions": 0, "withheld": 0}
        self._ocr_status = "enabled"
        self._shot_threads = []
        self._stop_event.clear()
        self._last_fg_hwnd = 0
        self._last_kdown_fg_hwnd = 0
        self._last_fg_title = ""
        self._event_seq = 0
        self._start_epoch = time.time()
        self.recording = True
        self._write_meta("recording", {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        # Start a fresh append-only evidence stream. Each completed step is
        # appended immediately so an interrupted session remains recoverable.
        (self.output_dir / "events.jsonl").write_text("", encoding="utf-8")
        if self._uia_worker is None:
            self._uia_worker = UiaWorker()
        self.hook.on_pointer_down = self._uia_worker.submit
        self.hook.start()
        self._proc_thread = threading.Thread(target=self._loop, daemon=True)
        self._proc_thread.start()
        log.info("录制开始: %s", self.output_dir)

    def stop(self):
        self.recording = False
        self._stop_event.set()
        # UWP 等应用的输入管线会延迟处理注入事件，先排空队列再收尾
        self._drain_quiet(2.0)
        self.hook.stop()
        self.hook.on_pointer_down = None
        # 钩子线程退出后，处理队列中延迟到达的剩余事件
        self._drain_quiet(1.0)
        if self._proc_thread:
            self._proc_thread.join(timeout=3.0)
        log.info("stop: pending_text=%d chars pending_scroll=%r 队列剩余=%d",
                 len(str(self._pending_text.get("text", "") if self._pending_text else "")),
                 bool(self._pending_scroll), self.hook._queue.qsize())
        # 冲刷未完成状态
        self._flush_pending()
        # 等后台截图线程收尾。截图落盘前要过 OCR 敏感遮挡（串行锁，
        # 密集全屏约 1.8s/帧），连点积压时 3s 不够 -> 提到 8s 并在
        # 超时（极罕见）时告警，保证 meta 审计计数与落盘一致。
        deadline = time.time() + 8.0
        for t in self._shot_threads:
            t.join(timeout=max(0.1, deadline - time.time()))
        if any(t.is_alive() for t in self._shot_threads):
            log.warning("截图 OCR 队列未在 8s 内收尾，部分截图可能延迟落盘")
        # 保存
        path = self.output_dir / "events.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"steps": self.steps, "count": len(self.steps)},
                      f, ensure_ascii=False, indent=2)
        self._write_meta("ready", {
            "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "step_count": len(self.steps),
            "screenshot_count": self._shot_idx,
            "privacy": {
                "text_masking": "enabled",
                "screenshot_ocr": self._ocr_status,
                "frames_redacted": self._frame_stats["redacted"],
                "regions_blurred": self._frame_stats["regions"],
                "frames_withheld": self._frame_stats["withheld"],
            },
        })
        log.info("录制完成: %s, %d 步", path, len(self.steps))
        return str(path)

    def _write_meta(self, state: str, extra: dict | None = None):
        """持久化可恢复的会话状态；写临时文件后替换，避免崩溃留下半个 JSON。"""
        meta_path = self.output_dir / "meta.json"
        try:
            current = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except Exception:
            current = {}
        current.update({"state": state, "heartbeat": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        if extra:
            current.update(extra)
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(meta_path)

    def _drain_quiet(self, max_wait):
        """停止前排空钩子队列：处理剩余事件，直到连续 0.5s 无新事件"""
        deadline = time.time() + max_wait
        last_seen = time.time()
        while time.time() < deadline:
            evt = self.hook.get_event(timeout=0.1)
            if evt is not None:
                try:
                    self._process(evt)
                except Exception:
                    log.exception("drain 处理事件异常")
                last_seen = time.time()
            elif time.time() - last_seen > 0.5:
                break

    def _loop(self):
        # 子线程中使用 win32com 需要先 CoInitialize
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            pass
        while not self._stop_event.is_set():
            evt = self.hook.get_event(timeout=0.02)
            if evt is None:
                now = int(time.time() * 1_000_000) - self.hook._start_us
                try:
                    self._check_timeouts(now)
                except Exception:
                    log.exception("check_timeouts 异常")
                continue
            try:
                self._process(evt)
            except Exception:
                log.exception("_process 处理事件异常")

    def _process(self, evt):
        # Ignore interactions with the TAGENT control surface itself. This is
        # especially important when the UI is opened in Brave/Edge: the
        # record and stop buttons must never become replay steps.
        et = evt.event_type
        pointer_event = et in (LBDOWN, LBUP, RBDOWN, RBUP, WHEEL)
        title = ((evt.target_title if pointer_event else evt.fg_title) or "").lower()
        if any(marker.lower() in title for marker in self._SELF_WINDOW_MARKERS):
            return
        if evt.fg_hwnd:
            self._last_fg_hwnd = evt.fg_hwnd
        if evt.fg_title:
            self._last_fg_title = evt.fg_title
        # 更新修饰键
        if et == KDOWN:
            if evt.vk in MODIFIER_VKS:
                self._held_mods.add(evt.vk)
            elif evt.fg_hwnd:
                # 非修饰键首按：记录组合键/功能键按下时刻的前台窗口
                # （KUP 时窗口可能已因该键生效而关闭，如 Enter 提交保存对话框）
                self._last_kdown_fg_hwnd = evt.fg_hwnd
        elif et == KUP:
            self._held_mods.discard(evt.vk)

        if et == LBDOWN:
            # 鼠标点击时先冲刷未完成的文本输入
            if self._pending_text:
                try:
                    self._finalize_text(self._pending_text)
                finally:
                    self._pending_text = None
            self._pending_click = self._pointer_context(evt)
        elif et == LBUP:
            self._on_left_up(evt)
        elif et == RBDOWN:
            self._pending_click = self._pointer_context(evt)
        elif et == RBUP:
            self._on_right_up(evt)
        elif et == WHEEL:
            self._on_scroll(evt)
        elif et == KDOWN:
            self._on_key_down(evt)
        elif et == KUP:
            self._on_key_up(evt)

    def _on_left_up(self, evt):
        pc = self._pending_click
        self._pending_click = None
        if not pc:
            return
        sx, sy, st = pc["x"], pc["y"], pc["t_us"]
        dist = ((evt.x - sx) ** 2 + (evt.y - sy) ** 2) ** 0.5
        dur = evt.t_us - st
        if dist < CLICK_DIST:
            action = "click"
        elif dur < SWIPE_TIME:
            action = "swipe"
        else:
            action = "drag"
        self._add_step(action, sx, sy, end_x=evt.x, end_y=evt.y, duration_us=dur, **pc["context"])

    def _on_right_up(self, evt):
        pc = self._pending_click
        self._pending_click = None
        if not pc:
            return
        sx, sy = pc["x"], pc["y"]
        self._add_step("right_click", sx, sy, **pc["context"])

    def _pointer_context(self, evt):
        """Capture the window/control under the pointer before click-up.

        The foreground window is not the click target (notably after Show
        Desktop and inside transient Shell menus).  Capturing on button-down
        also preserves menu-item UIA evidence before the menu disappears.
        """
        target = evt.target_hwnd or win32gui.WindowFromPoint((evt.x, evt.y))
        root, title, size = self._get_window_info(target) if target else (None, "", None)
        client = (None, None)
        if root:
            try:
                client = win32gui.ScreenToClient(root, (evt.x, evt.y))
            except Exception:
                pass
        uia_evidence = self._uia_worker.resolve(
            evt.pointer_evidence, UIA_GRAB_TIMEOUT
        ) if self._uia_worker and evt.pointer_evidence else self._uia_grab_async(evt.x, evt.y)
        return {
            "x": evt.x,
            "y": evt.y,
            "t_us": evt.t_us,
            "context": {
                "fg_hwnd": evt.fg_hwnd,
                "target_hwnd": root or target,
                "target_title": title or evt.target_title,
                "target_class": evt.target_class,
                "target_size": size,
                "target_client": client,
                "uia_evidence": uia_evidence,
            },
        }

    def _on_scroll(self, evt):
        now_us = evt.t_us
        if self._pending_scroll and (now_us - self._pending_scroll["t_us"] < SCROLL_MERGE):
            self._pending_scroll["delta"] += evt.delta
            self._pending_scroll["t_us"] = now_us
        else:
            if self._pending_scroll:
                self._finalize_scroll(self._pending_scroll)
            self._pending_scroll = {"x": evt.x, "y": evt.y, "delta": evt.delta, "t_us": now_us}

    def _on_key_down(self, evt):
        vk = evt.vk
        if vk in MODIFIER_VKS:
            return
        # 仅 Shift 按下时，字母/数字仍走文本输入路径
        only_shift = self._held_mods and self._held_mods <= {0x10, 0xA0, 0xA1}
        if vk in FUNC_VKS or (self._held_mods and not only_shift):
            # 功能键或非 Shift 组合键，等 KEYUP 时处理
            return
        # 可打印字符 -> 文本输入
        char = vk_to_char(vk, bool(self._held_mods))
        log.debug("key down vk=%#x char=%r pending=%r", vk, char, self._pending_text)
        if char:
            if self._pending_text:
                gap = evt.t_us - self._pending_text["t_us"]
                if gap < TEXT_TIMEOUT:
                    self._pending_text["text"] += char
                    self._pending_text["t_us"] = evt.t_us
                else:
                    self._finalize_text(self._pending_text)
                    self._pending_text = {"text": char, "t_us": evt.t_us, "fg_hwnd": self._last_fg_hwnd}
            else:
                self._pending_text = {"text": char, "t_us": evt.t_us, "fg_hwnd": self._last_fg_hwnd}

    def _on_key_up(self, evt):
        vk = evt.vk
        log.info("keyup vk=%#x held=%r fg=%s", vk, sorted(self._held_mods), hex(evt.fg_hwnd))
        # 仅 Shift 释放时直接返回
        if vk in {0x10, 0xA0, 0xA1}:
            return
        # 仅 Shift 不算组合键
        real_mods = self._held_mods - {0x10, 0xA0, 0xA1}
        # 功能键或组合键记录前，先冲刷 pending text（保持时间顺序）
        if (real_mods and vk not in MODIFIER_VKS) or vk in FUNC_VKS:
            if self._pending_text:
                try:
                    self._finalize_text(self._pending_text)
                finally:
                    self._pending_text = None
        if real_mods and vk not in MODIFIER_VKS:
            mod_names = list(dict.fromkeys(vk_to_name(m) for m in sorted(real_mods)))
            key_name = vk_to_name(vk)
            combo = " ".join(mod_names + [key_name])
            self._add_step("key_press", key=combo, fg_hwnd=self._last_kdown_fg_hwnd)
        elif vk in FUNC_VKS:
            self._add_step("key_press", key=vk_to_name(vk), fg_hwnd=self._last_kdown_fg_hwnd)

    def _check_timeouts(self, now_us):
        if self._pending_scroll and (now_us - self._pending_scroll["t_us"] > SCROLL_MERGE):
            self._finalize_scroll(self._pending_scroll)
            self._pending_scroll = None
        if self._pending_text and (now_us - self._pending_text["t_us"] > TEXT_TIMEOUT):
            try:
                self._finalize_text(self._pending_text)
            finally:
                self._pending_text = None

    def _finalize_scroll(self, s):
        self._add_step("scroll", s["x"], s["y"], delta=s["delta"])

    def _finalize_text(self, t):
        # 日志只记长度:键入原文可能含敏感值,task.json 才是落盘位置(且过 PII 脱敏)
        log.info("finalize_text (%d chars, recording=%s)",
                 len(str(t.get("text", ""))), self.recording)
        try:
            step = self._add_step("input_text", text=t["text"], fg_hwnd=t.get("fg_hwnd", 0))
            # 阶段4.3 安全：若输入目标是密码框，明文不落盘，替换为占位
            if step is not None:
                sec = _get_security()
                hwnd = step.get("hwnd") or win32gui.GetForegroundWindow()
                if hwnd and sec and sec.is_password_edit(hwnd):
                    step["text"] = "{{PASSWORD}}"
                    step["sensitive"] = True
                    log.info("检测到密码框，本次输入不落明文")
                else:
                    masked = mask_pii(step.get("text", ""))
                    if masked.hit:
                        step["text"] = masked.text
                        step["sensitive"] = True
                        step["pii_kinds"] = sorted({m.kind for m in masked.matches})
                        log.info("检测到 PII(%s)，已掩码", step["pii_kinds"])
                        # 原始值只留在内存，供截图 OCR 遮挡 cross-feed（不上屏、不落盘）
                        if len(self._known_secrets) < 200:
                            self._known_secrets.update(m.value for m in masked.matches)
        except Exception:
            log.exception("finalize_text 异常")

    def _flush_pending(self):
        if self._pending_scroll:
            self._finalize_scroll(self._pending_scroll)
            self._pending_scroll = None
        if self._pending_text:
            log.info("flush_pending text %r", self._pending_text["text"])
            self._finalize_text(self._pending_text)
            self._pending_text = None

    def _get_window_info(self, hwnd, x=None, y=None):
        """容错取窗口信息：句柄可能失效（钩子回调抓取的前台窗口瞬间销毁）"""
        try:
            hwnd = win32gui.GetAncestor(hwnd, 2)  # GA_ROOT
            window_title = win32gui.GetWindowText(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
            win_size = (rect[2] - rect[0], rect[3] - rect[1])
            return hwnd, window_title, win_size
        except Exception:
            return None, "", None

    def _add_step(self, action, x=None, y=None, **extra):
        """记录一步操作 + 截图 + 窗口信息"""
        now_us = int(time.time() * 1_000_000) - self.hook._start_us
        elapsed = round(now_us / 1_000_000, 3)
        step = {
            "action": action,
            "t": elapsed,
            # Preserve the actual action time, not the time the processing
            # thread happened to flush the event.
            "epoch": round(self._start_epoch + elapsed, 6),
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S%z", time.localtime(self._start_epoch + elapsed)
            ),
        }

        # 跨窗口/等待检测（阶段2.2/2.3）：在动作步骤前插入 switch_window / wait
        if self.steps:
            prev = self.steps[-1]
            prev_t = prev.get("t", 0)
            gap = round(now_us / 1_000_000, 3) - prev_t
            if gap >= WAIT_THRESHOLD:
                    synthetic = {
                        "action": "wait",
                        "t": round(now_us / 1_000_000, 3),
                        "duration": round(gap, 1),
                        "window": prev.get("window", ""),
                    }
                    self.steps.append(synthetic)
                    self._append_event(synthetic)

        # 获取窗口信息
        hwnd = None
        window_title = ""
        win_size = None
        if x is not None and y is not None:
            # 鼠标动作属于指针下方窗口，不属于点击前的前台窗口。
            # button-down 快照还可保留点击后已经消失的菜单/对话框信息。
            hwnd = extra.get("target_hwnd") or win32gui.WindowFromPoint((x, y))
            captured_title = extra.get("target_title", "")
            captured_size = extra.get("target_size")
            if hwnd and win32gui.IsWindow(hwnd):
                hwnd, window_title, win_size = self._get_window_info(hwnd)
            else:
                window_title, win_size = captured_title, captured_size
            if not window_title:
                window_title = captured_title
            if not win_size:
                win_size = captured_size
            # 屏幕坐标转百分比
            sw, sh = _screen_size()
            step["pct_x"] = round(x / sw * 100, 1) if sw else 0
            step["pct_y"] = round(y / sh * 100, 1) if sh else 0
            step["screen_x"] = x
            step["screen_y"] = y

            # 转客户区坐标
            target_client = extra.get("target_client")
            if target_client and target_client != (None, None):
                step["client_x"], step["client_y"] = target_client
            elif hwnd and win32gui.IsWindow(hwnd):
                try:
                    cx, cy = win32gui.ScreenToClient(hwnd, (x, y))
                    step["client_x"] = cx
                    step["client_y"] = cy
                except Exception:
                    pass
        else:
            # 键盘动作（input_text/key_press）：捕获前台窗口，回放时用于定位焦点
            # 优先用事件发生时（钩子回调时刻）抓取的前台窗口，避免队列延迟导致归属错乱
            hwnd = extra.get("fg_hwnd") or self._last_fg_hwnd or win32gui.GetForegroundWindow()
            if hwnd:
                hwnd, window_title, win_size = self._get_window_info(hwnd)
            if not hwnd and self._last_fg_title:
                # 句柄已失效（如回车提交后对话框关闭），用事件时刻缓存的前台标题
                window_title = self._last_fg_title

        step["hwnd"] = hwnd
        step["window"] = window_title
        step["win_size"] = win_size
        if extra.get("target_class"):
            step["target_class"] = extra["target_class"]

        # 窗口切换检测：动作目标窗口与前一步窗口不同 -> 插入 switch_window（阶段2.2）
        if hwnd and window_title and self.steps:
            for s in reversed(self.steps):
                if s["action"] not in ("wait", "switch_window"):
                    prev_win = s.get("window", "")
                    prev_hwnd = s.get("hwnd")
                    break
            else:
                prev_win, prev_hwnd = "", None
            if prev_hwnd and hwnd != prev_hwnd:
                synthetic = {
                    "action": "switch_window",
                    "t": round(now_us / 1_000_000, 3),
                    "window": window_title,
                    "hwnd": hwnd,
                    "win_size": win_size,
                }
                self.steps.append(synthetic)
                self._append_event(synthetic)

        # UIA 证据只对鼠标动作抓取（键盘回放用 WM_CHAR，不依赖定位）
        # 且 UIA 在大应用(如浏览器)上很慢，必须异步+超时
        if action in ("click", "right_click", "double_click", "scroll"):
            step["uia"] = extra.get("uia_evidence") or self._uia_grab_async(x, y)
        else:
            step["uia"] = None

        # 合并额外字段
        internal = {"fg_hwnd", "target_hwnd", "target_title", "target_class",
                    "target_size", "target_client", "uia_evidence"}
        for k, v in extra.items():
            if k not in step and k not in internal:
                step[k] = v

        # 截图（仅鼠标动作截图；渲染较重，放后台线程避免阻塞事件分类）
        if action in ("click", "right_click", "double_click", "scroll"):
            shot_name = f"shot_{self._shot_idx:04d}.jpg"
            self._shot_idx += 1
            step["screenshot"] = shot_name
            win_size = step.get("win_size")
            client_x = step.get("client_x")
            client_y = step.get("client_y")
            pct_x, pct_y = step.get("pct_x"), step.get("pct_y")
            renderer = self._renderer
            shot_dir = self.shot_dir

            def _shoot():
                img = capture_window(hwnd) if hwnd else None
                if img is None:
                    img = capture_screen()
                if img is None:
                    return
                img = renderer.draw_grid(img)
                # 坐标标记
                if client_x is not None and win_size:
                    mp_x = round(client_x / win_size[0] * 100, 1)
                    mp_y = round(client_y / win_size[1] * 100, 1)
                else:
                    mp_x, mp_y = pct_x, pct_y
                img = renderer.draw_marker(img, mp_x, mp_y)
                # 阶段4.4 截屏压缩：JPEG quality=70 替代 PNG（体积减小 ~90%）
                shot_name_jpg = shot_name.replace(".png", ".jpg")
                # JPEG 不支持 RGBA/透明通道，需先转换为 RGB
                save_img = img.convert("RGB") if img.mode != "RGB" else img
                save_img = self._redact_before_save(save_img)
                save_img.save(str(shot_dir / shot_name_jpg), "JPEG", quality=70)

            t = threading.Thread(target=_shoot, daemon=True)
            t.start()
            self._shot_threads.append(t)

        self.steps.append(step)
        # Append every completed step immediately. _append_event makes a
        # redacted copy for input_text, so sensitive values never enter the
        # append-only stream in clear text.
        self._append_event(step)
        return step

    _ocr_lock = threading.Lock()  # 串行化截图 OCR（防连点导致多 tesseract 并发）

    def _redact_before_save(self, img):
        """落盘前对截图做敏感遮挡（gap 分析整改项：截图 OCR 遮挡）。

        - known values：本会话文本层 mask_pii 检出的原始值 cross-feed 到帧
        - OCR 引擎异常 -> withheld 计数（本地优先保留原图，审计里可见）
        """
        stats = self._frame_stats
        stats["frames"] += 1
        try:
            from services.frame_redactor import redact_image
            # 拿锁等待（截图线程本身在后台，排队不阻塞事件分类）
            with self._ocr_lock:
                img, audit = redact_image(img, known_values=self._known_secrets)
        except Exception as exc:
            log.warning("截图敏感遮挡不可用: %s", exc)
            self._ocr_status = "unavailable"
            return img
        if audit.get("withheld"):
            stats["withheld"] += 1
            if self._ocr_status == "enabled":
                self._ocr_status = "degraded"
                log.warning("截图 OCR 失败（%s），该帧未验证即落盘", audit.get("error"))
        if audit.get("redacted"):
            stats["redacted"] += 1
            stats["regions"] += audit.get("boxes", 0)
        return img

    def _append_event(self, step):
        try:
            path = self.output_dir / "events.jsonl"
            event_step = dict(step)
            if event_step.get("action") == "input_text" and not event_step.get("sensitive"):
                text = str(event_step.get("text", ""))
                hwnd = event_step.get("hwnd") or win32gui.GetForegroundWindow()
                sec = _get_security()
                if hwnd and sec and sec.is_password_edit(hwnd):
                    event_step["text"] = "{{PASSWORD}}"
                    event_step["sensitive"] = True
                else:
                    masked = mask_pii(text)
                    if masked.hit:
                        event_step["text"] = masked.text
                        event_step["sensitive"] = True
                        event_step["pii_kinds"] = sorted({m.kind for m in masked.matches})
            with self._event_lock:
                event = {
                    "seq": self._event_seq,
                    "t": event_step.get("t", 0),
                    "epoch": event_step.get("epoch") or time.time(),
                    "timestamp": event_step.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "type": event_step.get("action", "unknown"),
                    "source": "windows-hook",
                    "payload": event_step,
                }
                self._event_seq += 1
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            log.exception("append events.jsonl failed")

    def _uia_grab_async(self, x, y):
        """通过专用工作线程抓取 UIA，短超时保护（慢时放弃，不阻塞分类）"""
        if self._uia_worker is None:
            return None
        return self._uia_worker.grab(x, y, timeout=UIA_GRAB_TIMEOUT)


def _screen_size():
    import ctypes
    try:
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        return sw, sh
    except Exception:
        return 0, 0


def _get_security():
    """懒加载安全模块（避免循环导入）"""
    try:
        from services.security import get_security
        return get_security()
    except Exception:
        return None
