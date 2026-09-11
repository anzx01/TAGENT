"""视觉智能接管（VisualAgent）

架构借鉴 mano-skill（MIT-0）：模型只做决策、客户端只做执行。
  截图(缩放) -> 视觉模型(OpenAI 兼容多模态 API, 用户可指定) -> 动作 JSON
  -> 本地注入执行 -> 循环直至 done/fail/步数上限

与 mano 云端不同：模型直连（api_base/api_key/model 全部用户可配），
执行复用 TAGENT 注入通道（剪贴板粘贴输入、keybd_event 组合键、
mouse_event 点击滚动拖拽），坐标按截图缩放比精确映射回物理屏幕。

配置：data/llm_config.json（与 LLM 自愈共用；可选 visual_model 指定视觉模型）
  {"api_base": "https://xxx/v1", "api_key": "sk-...",
   "model": "glm-4.6v", "vision_width": 1280}
"""
import base64
import ctypes
import io
import json
import re
import threading
import time
import urllib.request
from typing import Callable, Optional

from PIL import Image

import paths
from capture import capture_screen
from logger import get_logger
from services.input.common import VK_MAP

log = get_logger("visual_agent")

u32 = ctypes.windll.user32
KEYEVENTF_KEYUP = 0x0002

# 线程级物理坐标系（Win10 1703+）。mss 截图永远是物理像素，而 DPI unaware
# 进程的窗口/鼠标 API 是逻辑坐标（225% 缩放下差 2.25 倍，点击会全部飞掉）。
# SetThreadDpiAwarenessContext 只影响当前线程、可运行中切换，没有
# SetThreadDesktop 那种"切换后注入失效"的副作用。
_DPI_CTX_PER_MONITOR_V2 = ctypes.c_void_p(-4)


def _enter_physical_dpi() -> bool:
    """当前线程切换到 Per-Monitor-V2 感知，坐标 API 变为物理像素"""
    try:
        r = u32.SetThreadDpiAwarenessContext(_DPI_CTX_PER_MONITOR_V2)
        return bool(r)
    except Exception:
        return False


MAX_STEPS_DEFAULT = 30
STEP_INTERVAL = 1.2          # 每个动作执行后的等待（秒）
MAX_HISTORY = 6              # 提供给模型的动作历史条数

SYSTEM_PROMPT = """你是一个 Windows 桌面 GUI 自动化助手。你将看到当前屏幕截图，根据任务目标决定下一步操作。

每次只返回一个 JSON 对象（不要 markdown 代码块、不要多余文字），格式为以下之一：
{"thought":"一句话分析","action":"click","x":640,"y":320}
{"action":"double_click","x":640,"y":320}
{"action":"right_click","x":640,"y":320}
{"action":"type","text":"要输入的内容"}        // 粘贴到当前焦点框（中文安全）
{"action":"type","text":"内容","x":640,"y":320} // 先点击(x,y)再粘贴（推荐：点击+输入一步完成）
{"action":"hotkey","keys":"ctrl+s"}           // 组合键，支持 ctrl/alt/shift/win 与单键组合
{"action":"scroll","dy":-3,"x":640,"y":320}   // dy 负=向上滚；x/y 可省略（原位置）
{"action":"drag","from_x":100,"from_y":100,"to_x":500,"to_y":500}
{"action":"wait","seconds":1.5}
{"action":"done","summary":"任务完成情况"}
{"action":"fail","reason":"无法完成的原因"}

必须遵守：
- 每个 JSON 必须包含 action 字段；只返回 thought 而没有 action 是无效响应
- 坐标基于你看到的截图尺寸给出（截图可能被缩放过，系统会自动映射回真实屏幕）
- 每次动作后你会拿到最新截图；不要试图一次返回多个动作
- type 粘贴到当前焦点控件。填写输入框时推荐用带坐标的 type（先点击再粘贴，
  一步完成）；点击后无需确认焦点——截图看不到光标是正常的，粘贴结果以
  下一步截图中的文字为准
- 若粘贴后内容没有出现在目标输入框，换一个坐标重试带坐标的 type
- 若根据动作历史与当前截图判断任务目标已达成，立即返回 done，不要重复已完成的操作
- 确实无法完成返回 fail"""


class VisualAgent:
    """截图 -> 视觉模型 -> 注入执行 的循环执行器"""

    def __init__(self):
        self.local_step = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state: dict = {}

    # ---------- 配置 ----------

    def _load_config(self) -> dict:
        p = paths.BASE_DIR / "data" / "llm_config.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def configured(self) -> bool:
        cfg = self._load_config()
        return bool(cfg.get("api_base") and cfg.get("api_key"))

    # ---------- 生命周期 ----------

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self):
        self._stop.set()

    def start(self, task: str, max_steps: int = MAX_STEPS_DEFAULT,
              on_update: Optional[Callable] = None) -> bool:
        """启动后台执行线程；已在运行则拒绝"""
        if self.running:
            return False
        if not self.configured():
            raise RuntimeError("视觉模型未配置：请在设置中填写 api_base/api_key"
                               "（data/llm_config.json）")
        self._stop.clear()
        self.state = {"task": task, "status": "running", "steps": [],
                      "error": None, "started_at": time.time()}
        self._thread = threading.Thread(
            target=self._run, args=(task, max_steps, on_update),
            daemon=True, name="visual-agent")
        self._thread.start()
        return True

    # ---------- 主循环 ----------

    def _run(self, task: str, max_steps: int, on_update):
        # 本线程切换物理坐标系：截图(mss)是物理像素，点击/键入坐标必须同系
        if _enter_physical_dpi():
            log.info("视觉接管线程已切换 Per-Monitor-V2 物理坐标系")
        else:
            log.warning("线程 DPI 切换失败，坐标可能错位（旧系统？）")
        history = []
        invalid_streak = 0
        try:
            for step in range(max_steps):
                if self._stop.is_set():
                    self.state["status"] = "stopped"
                    break
                # 1) 截图（物理 -> 缩放）
                if self.local_step is not None and self.local_step():
                    history.append({"step": step, "action": "local_save", "note": "Local save dialog submitted; verify the saved document."})
                    time.sleep(1)
                    continue
                img = capture_screen()
                if img is None:
                    raise RuntimeError("截屏失败")
                real_w, real_h = img.size
                scale_w = int(self._load_config().get("vision_width", 1280))
                if real_w > scale_w:
                    ratio = scale_w / real_w
                    img = img.resize(
                        (scale_w, int(real_h * ratio)), Image.LANCZOS if hasattr(Image, "LANCZOS") else 1)
                else:
                    ratio = 1.0
                view_w, view_h = img.size

                # 2) 模型决策（缺 action/解析失败自动重试一次；
                #    通用 VL 模型常返回只有 thought 的 JSON，不应一击致命）
                repeat_hint = ""
                if len(history) >= 3 and len({h.get("signature") for h in history[-3:]}) == 1:
                    repeat_hint = "The last three actions were identical. Inspect the result and choose a different action. A single click selects a folder; double click opens it. In Save As, enter the full absolute path in the filename field instead of traversing folders."
                decision = self._decide(task, img, history, view_w, view_h, hint=repeat_hint)
                if decision is not None and not str(
                        decision.get("action", "")).strip():
                    decision = self._decide(
                        task, img, history, view_w, view_h,
                        hint="注意：上一次响应缺少 action 字段，是无效响应。"
                             "本次必须返回包含 action 字段的完整 JSON。")
                if decision is None or not str(
                        decision.get("action", "")).strip():
                    invalid_streak += 1
                    log.warning("[视觉接管 %d] 模型响应无效(%d/3)，跳过重试",
                                step, invalid_streak)
                    if invalid_streak >= 3:
                        raise RuntimeError("视觉模型连续 3 次无有效响应")
                    time.sleep(1.0)
                    continue
                invalid_streak = 0
                thought = str(decision.get("thought", ""))[:120]
                action = str(decision.get("action", "")).strip().lower()
                self.state["steps"].append(
                    {"step": step, "thought": thought, "action": action,
                     "raw": _safe(decision), "ts": time.strftime("%H:%M:%S")})
                log.info("[视觉接管 %d] %s %s", step, action, thought)
                if on_update:
                    try:
                        on_update(self.state)
                    except Exception:
                        pass

                # 3) 终止判定
                if action == "done":
                    self.state["status"] = "done"
                    self.state["summary"] = str(decision.get("summary", ""))
                    break
                if action == "fail":
                    self.state["status"] = "fail"
                    self.state["error"] = str(decision.get("reason", ""))
                    break
                if action not in _EXECUTORS:
                    history.append({"step": step, "action": action,
                                    "note": "unknown_action_skipped"})
                    continue

                # 4) 执行（坐标从视图坐标映射回物理坐标）
                ok, msg = _EXECUTORS[action](decision, ratio)
                history.append({"step": step, "action": action,
                                "thought": thought, "result": msg,
                                "signature": json.dumps({k: v for k, v in decision.items() if k != "thought"}, sort_keys=True)})
                history = history[-MAX_HISTORY:]
                time.sleep(_action_delay(action, decision))
            else:
                self.state["status"] = "max_steps"
        except Exception as e:
            self.state["status"] = "error"
            self.state["error"] = str(e)
            log.exception("视觉接管异常")
        finally:
            self.state["finished_at"] = time.time()
            if on_update:
                try:
                    on_update(self.state)
                except Exception:
                    pass
            log.info("视觉接管结束: %s (%s)", self.state.get("status"),
                     self.state.get("summary") or self.state.get("error") or "")

    # ---------- 模型调用 ----------

    def _decide(self, task: str, img, history: list, view_w: int, view_h: int,
                hint: str = "") -> dict | None:
        cfg = self._load_config()
        api_base = cfg["api_base"].rstrip("/")
        model = cfg.get("visual_model") or cfg.get("model", "gpt-4o")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        hist_text = "\n".join(
            f"{h.get('step')}. {h.get('action')} | {h.get('thought') or h.get('note', '')}"
            for h in history) or "（无，这是第一步）"
        user_text = (f"任务：{task}\n\n已执行动作历史：\n{hist_text}\n\n"
                     f"当前截图尺寸 {view_w}x{view_h}（坐标按此截图给出）。"
                     f"请返回下一步动作 JSON。")
        if hint:
            user_text += "\n\n" + hint

        system_prompt = SYSTEM_PROMPT
        if cfg.get("action_format") == "uitars":
            mode = cfg.get("coordinate_mode", "pixels")
            system_prompt = ("You are a desktop GUI agent. Use the screenshot and action history. "
                             "Return one action only as Action: function(keyword=value). "
                             "Allowed: click(point='(x,y)'), left_double(point='(x,y)'), "
                             "right_single(point='(x,y)'), type(content='text'), hotkey(key='ctrl s'), "
                             "drag(start_point='(x,y)',end_point='(x,y)'), wait(), finished(content='summary'). "
                             f"Coordinate mode: {mode}. pixels means the supplied image dimensions; "
                             "normalized means 0..1; 1000 means 0..1000. Never execute shell commands.")
            user_text += "\nReturn UI-TARS Action syntax instead of JSON."

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}"}},
                ]},
            ],
            "temperature": 0.1,
            "max_tokens": 400,
        }
        req = urllib.request.Request(
            f"{api_base}/chat/completions", method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg['api_key']}"},
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"))
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        if "Action:" in content or cfg.get("action_format") == "uitars":
            from services.uitars_actions import parse_action
            try:
                return parse_action(content, view_w, view_h, cfg.get("coordinate_mode", "pixels"))
            except (ValueError, SyntaxError, TypeError):
                log.warning("Invalid UI-TARS action")
                return None
        return _parse_action_json(content)


# ---------- 动作执行（全局注入，坐标为视图坐标 * 1/ratio -> 物理） ----------

def _map_xy(decision: dict, ratio: float) -> tuple[int, int]:
    x = float(decision.get("x", 0))
    y = float(decision.get("y", 0))
    return int(round(x / ratio)), int(round(y / ratio))


def _mouse_click(x: int, y: int, double=False, right=False):
    u32.SetCursorPos(x, y)
    time.sleep(0.12)
    if right:
        u32.mouse_event(0x0008, 0, 0, 0, 0)
        time.sleep(0.04)
        u32.mouse_event(0x0010, 0, 0, 0, 0)
    else:
        for _ in range(2 if double else 1):
            u32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.04)
            u32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.08 if double else 0)
    time.sleep(0.15)
    return True, f"click({x},{y})"


def _exec_click(decision, ratio):
    x, y = _map_xy(decision, ratio)
    return _mouse_click(x, y)


def _exec_double_click(decision, ratio):
    x, y = _map_xy(decision, ratio)
    return _mouse_click(x, y, double=True)


def _exec_right_click(decision, ratio):
    x, y = _map_xy(decision, ratio)
    return _mouse_click(x, y, right=True)


def _set_clipboard_text(text: str) -> bool:
    import win32clipboard
    import win32con
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log.warning("剪贴板写入失败: %s", e)
        return False


def _paste():
    """Ctrl+V 全局注入（剪贴板粘贴，规避 IME 吃字母）"""
    u32.keybd_event(0x11, 0x1D, 0, 0)
    time.sleep(0.04)
    u32.keybd_event(0x56, 0x2F, 0, 0)
    time.sleep(0.04)
    u32.keybd_event(0x56, 0, KEYEVENTF_KEYUP, 0)
    u32.keybd_event(0x11, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)


def _exec_type(decision, ratio):
    text = str(decision.get("text", ""))
    if not text:
        return False, "type: text 为空"
    # 带坐标：先点击目标输入框再粘贴（一步完成，规避模型反复确认焦点）
    if decision.get("x") is not None and decision.get("y") is not None:
        x, y = _map_xy(decision, ratio)
        _mouse_click(x, y)
        time.sleep(0.3)
    if not _set_clipboard_text(text):
        return False, "type: 剪贴板不可用"
    _paste()
    return True, f"type({len(text)} 字符)"


def _exec_hotkey(decision, ratio):
    keys = str(decision.get("keys", "")).strip().lower()
    vks = _parse_keys(keys)
    if not vks:
        return False, f"hotkey: 无法解析 {keys!r}"
    # Win/右修饰键是扩展键：不带 KEYEVENTF_EXTENDEDKEY 会被系统丢弃（实测）
    EXTENDED = {0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
    for vk in vks:
        flags = 0x0001 if vk in EXTENDED else 0
        u32.keybd_event(vk, u32.MapVirtualKeyW(vk, 0), flags, 0)
        time.sleep(0.06)
    for vk in reversed(vks):
        flags = 0x0003 if vk in EXTENDED else KEYEVENTF_KEYUP
        u32.keybd_event(vk, 0, flags, 0)
        time.sleep(0.06)
    time.sleep(0.5)
    return True, f"hotkey({keys})"


def _parse_keys(keys: str) -> list:
    vks = []
    for part in re.split(r"[+\s]+", keys):
        p = part.strip().lower()
        if not p:
            continue
        if p in VK_MAP:
            vks.append(VK_MAP[p])
        elif len(p) == 1:
            vks.append(ord(p.upper()))
    return vks


def _exec_scroll(decision, ratio):
    try:
        dy = int(float(decision.get("dy", -1)))
    except (TypeError, ValueError):
        dy = -1
    if decision.get("x") is not None and decision.get("y") is not None:
        x, y = _map_xy(decision, ratio)
        u32.SetCursorPos(x, y)
        time.sleep(0.1)
    u32.mouse_event(0x0800, 0, 0, dy * 120, 0)   # MOUSEEVENTF_WHEEL
    time.sleep(0.4)
    return True, f"scroll(dy={dy})"


def _exec_drag(decision, ratio):
    try:
        fx = int(round(float(decision.get("from_x", 0)) / ratio))
        fy = int(round(float(decision.get("from_y", 0)) / ratio))
        tx = int(round(float(decision.get("to_x", 0)) / ratio))
        ty = int(round(float(decision.get("to_y", 0)) / ratio))
    except (TypeError, ValueError):
        return False, "drag: 坐标无效"
    u32.SetCursorPos(fx, fy)
    time.sleep(0.15)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    for i in range(1, 11):
        u32.SetCursorPos(int(fx + (tx - fx) * i / 10), int(fy + (ty - fy) * i / 10))
        time.sleep(0.03)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.3)
    return True, f"drag({fx},{fy}->{tx},{ty})"


def _exec_wait(decision, ratio):
    try:
        sec = min(max(float(decision.get("seconds", 1)), 0.2), 5.0)
    except (TypeError, ValueError):
        sec = 1.0
    time.sleep(sec)
    return True, f"wait({sec}s)"


_EXECUTORS = {
    "click": _exec_click,
    "double_click": _exec_double_click,
    "right_click": _exec_right_click,
    "type": _exec_type,
    "hotkey": _exec_hotkey,
    "scroll": _exec_scroll,
    "drag": _exec_drag,
    "wait": _exec_wait,
}


def _action_delay(action: str, decision: dict) -> float:
    """动作后基础间隔：界面敏感操作多等一会"""
    if action in ("click", "double_click", "right_click", "hotkey", "drag"):
        return STEP_INTERVAL + 0.8
    return STEP_INTERVAL


def _safe(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)[:300]
    except Exception:
        return str(obj)[:300]


def _parse_action_json(content: str) -> dict | None:
    """从模型输出提取动作 JSON（容忍 ```json 包裹/前后杂文）"""
    if not content:
        return None
    text = content.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if m:
            text = m.group(1)
    else:
        # 无围栏时找第一个 {...} 平衡块
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        text = text[start:i + 1]
                        break
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        log.warning("模型输出无法解析: %.200s", content)
        return None


_store: VisualAgent | None = None


def get_visual_agent() -> VisualAgent:
    global _store
    if _store is None:
        _store = VisualAgent()
    return _store
