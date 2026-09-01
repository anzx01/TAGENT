"""
编译器 - 将录制数据编译为带定位策略的 task.json
定位策略优先级：
  1. UI Automation 锚点（控件名+类型+AutomationId）
  2. 窗口内百分比 + 窗口标题（窗口移动/分辨率变化后仍有效）
  3. 屏幕百分比坐标兜底
"""
import json
from pathlib import Path

# 相邻 input_text 合并间隔（秒）
INPUT_MERGE_GAP = 5.0

# 可变输入推断关键词（移植 openskills heuristic.ts 参数推断）
PARAM_KEYWORDS = ("搜索", "search", "查找", "账号", "user", "account", "用户名",
                  "邮箱", "email", "手机", "phone", "验证码", "code",
                  "关键词", "keyword", "query", "地址", "address")


class Compiler:
    def __init__(self, recording_dir):
        self.dir = Path(recording_dir)
        events_path = self.dir / "events.json"
        with open(events_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.steps = data.get("steps", [])

    def compile(self):
        """编译录制数据为 task.json，过滤冗余步骤 + 语义折叠"""
        task_steps = []
        prev_window = None
        prev_step = None
        prev_click_pos = None
        prev_click_time = 0
        segment = 0
        first_in_segment = True

        def sign_segment():
            nonlocal segment, first_in_segment
            segment += 1
            first_in_segment = True

        for i, step in enumerate(self.steps):
            # 语义段锚点：切换到新窗口时开新段（移植 skill-recorder bundle.ts）
            window = step.get("window", "")
            if prev_window is not None and window != prev_window:
                sign_segment()
            prev_window = window

            # 双击合并：同位置、间隔 < 0.5s 的两个 click -> double_click（保留第一击的定位）
            if (step.get("action") == "click" and prev_step
                    and prev_step.get("action") == "click"
                    and (step.get("screen_x"), step.get("screen_y"))
                        == (prev_step.get("screen_x"), prev_step.get("screen_y"))
                    and step.get("t", 0) - prev_step.get("t", 0) < 0.5):
                merged = dict(prev_step, action="double_click")
                task_steps[-1] = self._compile_step(merged, len(task_steps) - 1)
                prev_step = None
                continue

            # 连续输入合并：同一窗口的相邻 input_text 折叠为一步
            # （移植 openskills heuristic.ts：连续 input 事件合并为一个 fill）
            if (step.get("action") == "input_text" and task_steps
                    and task_steps[-1].get("action") == "input_text"
                    and task_steps[-1].get("segment") == segment):
                last = task_steps[-1]
                same_win = bool(last.get("hwnd")) and last.get("hwnd") == step.get("hwnd")
                if not same_win:
                    same_win = (not last.get("hwnd")) and bool(window) \
                        and last.get("window") == window
                if same_win and step.get("t", 0) - last.get("t", 0) < INPUT_MERGE_GAP:
                    tail = dict(last)
                    tail["text"] = (tail.get("text", "") or "") + (step.get("text", "") or "")
                    tail["t"] = step.get("t", 0)
                    task_steps[-1] = tail
                    prev_step = step
                    continue

            # 过滤冗余 switch_window（连续相同窗口）
            if step.get("action") == "switch_window":
                window = step.get("window", "")
                if window == prev_window:
                    continue
                prev_window = window

            # 过滤冗余 click（相同位置 0.5~1 秒内重复；<0.5s 已在上面合并为双击）
            if step.get("action") == "click":
                pos = (step.get("screen_x"), step.get("screen_y"))
                t = step.get("t", 0)
                if pos == prev_click_pos and 0.5 <= (t - prev_click_time) < 1.0:
                    continue
                prev_click_pos = pos
                prev_click_time = t

            # 任务栏点击转换（win_size[1] < 100px = 任务栏）
            if step.get("action") == "click" and step.get("win_size"):
                _, h = step["win_size"]
                if h < 100:
                    step = dict(step)
                    step["_taskbar_click"] = True

            compiled = self._compile_step(step, len(task_steps))
            compiled["segment"] = segment
            if first_in_segment and step.get("action") not in ("wait", "switch_window"):
                compiled["segment_lead"] = True
                first_in_segment = False
            task_steps.append(compiled)
            prev_step = step

        task = {
            "version": 2,
            "source": self.dir.name,
            "step_count": len(task_steps),
            "segment_count": segment + 1,
            "steps": task_steps,
        }
        # 弱定位警告（移植 openskills isWeakSelector）：全程无高质量定位
        located = [s for s in task_steps if s.get("locator")]
        if located and max((s["locator"][0].get("confidence", 0) for s in located), default=1) < 0.6:
            task["warning"] = "weak_locators"

        task_path = self.dir / "task.json"
        with open(task_path, "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, indent=2)
        return str(task_path)

    def _compile_step(self, step, index):
        """编译单步：标注定位策略"""
        compiled = {
            "index": index,
            "action": step.get("action"),
            "t": step.get("t", 0),
        }

        # 复制动作特定字段
        for key in ("text", "key", "delta", "end_x", "end_y", "duration_us", "hwnd"):
            if key in step:
                compiled[key] = step[key]

        # 等待/切换窗口步骤：仅复制语义字段，无需定位
        if step.get("action") == "wait":
            for key in ("duration", "window"):
                if key in step:
                    compiled[key] = step[key]
            return compiled
        if step.get("action") == "switch_window":
            for key in ("window", "hwnd"):
                if key in step:
                    compiled[key] = step[key]
            return compiled

        # 生成定位策略
        compiled["locator"] = self._build_locator(step)

        # 可变输入建议参数（不替换 text，由用户在 UI 决定是否参数化）
        if step.get("action") == "input_text":
            cfg = self._infer_param(step)
            if cfg:
                compiled["suggest_param"] = cfg

        # 阶段4 安全效果标签（借鉴 skillwright）
        compiled["effect"] = self._classify_effect(step)

        # 保留原始坐标作为兜底
        for key in ("pct_x", "pct_y", "screen_x", "screen_y",
                    "client_x", "client_y", "window", "win_size"):
            if key in step:
                compiled[key] = step[key]

        # 截图引用
        if "screenshot" in step:
            compiled["screenshot"] = step["screenshot"]

        return compiled

    # 安全效果分类关键词
    DESTRUCTIVE_KEYWORDS = ("delete", "remove", "clear", "format", "drop", "truncate",
                           "清空", "删除", "格式化", "卸载", "永久")
    MUTATING_KEYWORDS = ("input", "type", "click", "press", "save", "send", "submit",
                         "drag", "scroll", "paste")

    def _classify_effect(self, step) -> str:
        """阶段4 安全效果标签：readonly / mutating / destructive"""
        action = step.get("action", "")
        text = (step.get("text", "") or "").lower()
        key = (step.get("key", "") or "").lower()
        combined = f"{action} {text} {key}"
        # 破坏性：含删除/清空/格式化等关键词
        for kw in self.DESTRUCTIVE_KEYWORDS:
            if kw in combined:
                return "destructive"
        # 只读：wait / switch_window / 无动作
        if action in ("wait", "switch_window") or not action:
            return "readonly"
        # 修改：其他所有动作（click/input/scroll 等）
        return "mutating"

    def _build_locator(self, step):
        """构建定位策略链：UIA > DOM > 坐标百分比 > OCR > 屏幕坐标
        每级带置信度（移植 openskills selectors.ts 0.35~0.95），按置信度降序"""
        strategies = []
        uia = step.get("uia")

        # 策略1: UI Automation 锚点（带动态 auto_id 过滤）
        if uia and (uia.get("name") or uia.get("auto_id") or uia.get("class")):
            auto_id = uia.get("auto_id", "")
            if auto_id and self._is_dynamic_id(auto_id):
                auto_id = ""
            strat = {
                "type": "uia",
                "name": uia.get("name", ""),
                "control_type": uia.get("type", ""),
                "class": uia.get("class", ""),
                "auto_id": auto_id,
            }
            if "parents" in uia:
                strat["parents"] = uia["parents"]
            if "sibling_index" in uia:
                strat["sibling_index"] = uia["sibling_index"]
            # 置信度：name+类型+class/auto_id 齐全最高，仅名称其次
            solid = (strat["name"] and (strat["class"] or strat["auto_id"]))
            strat["confidence"] = 0.95 if solid else 0.9
            strategies.append(strat)

            # 策略2: DOM 定位（仅浏览器窗口，阶段2.2）
            window_title = step.get("window", "")
            from services.dom_locator import is_browser_window, build_dom_locator
            if is_browser_window(window_title):
                dom_loc = build_dom_locator(uia)
                if dom_loc:
                    dom_loc["confidence"] = 0.88
                    strategies.append(dom_loc)
                else:
                    # Chrome/Edge 的 UIA 桥不暴露页面控件文本，
                    # 改用 CDP elementFromPoint 抓取点击点 DOM 特征
                    try:
                        from services.web_dom import build_dom_locator_from_cdp
                        loc = build_dom_locator_from_cdp(
                            window_title, step.get("screen_x"), step.get("screen_y"))
                        if loc:
                            loc["confidence"] = 0.88
                            strategies.append(loc)
                    except Exception:
                        pass

        # 策略3: 窗口内百分比 + 窗口标题
        if "client_x" in step and "client_y" in step and step.get("win_size"):
            w, h = step["win_size"]
            if w > 0 and h > 0:
                strategies.append({
                    "type": "coord",
                    "client_pct_x": round(step["client_x"] / w * 100, 1),
                    "client_pct_y": round(step["client_y"] / h * 100, 1),
                    "window": step.get("window", ""),
                    "confidence": 0.75,
                })

        # 策略4: 屏幕百分比坐标
        if "pct_x" in step and "pct_y" in step:
            strategies.append({
                "type": "coord",
                "pct_x": step["pct_x"],
                "pct_y": step["pct_y"],
                "window": step.get("window", ""),
                "confidence": 0.6,
            })

        # 策略5: OCR 文本兜底（阶段1.1）
        if uia and uia.get("name"):
            strategies.append({
                "type": "ocr",
                "text": uia.get("name", ""),
                "window": step.get("window", ""),
                "confidence": 0.55,
            })

        # 策略6: 纯坐标兜底
        if "screen_x" in step:
            strategies.append({
                "type": "screen_coord",
                "x": step["screen_x"],
                "y": step["screen_y"],
                "confidence": 0.35,
            })

        strategies.sort(key=lambda s: s.get("confidence", 0), reverse=True)
        return strategies

    DYNAMIC_ID_PREFIXES = ("react", "mui", "ember", "ng-", "auto_", "_")

    def _is_dynamic_id(self, auto_id: str) -> bool:
        """过滤动态 auto_id（移植 openskills hasDynamicId）"""
        low = auto_id.lower()
        return any(low.startswith(p) for p in self.DYNAMIC_ID_PREFIXES) \
            or (len(low) >= 8 and low.isdigit())

    def _infer_param(self, step):
        """推断可变输入（移植 openskills heuristic 参数推断）：
        输入控件名称命中关键词 -> 建议参数化，防把真实业务值硬编码进录制"""
        uia = step.get("uia") or {}
        name = uia.get("name", "") or ""
        text = step.get("text", "")
        if not text or not name:
            return None
        low = name.lower()
        key = next((k for k in PARAM_KEYWORDS if k in low), None)
        if not key:
            return None
        return {"key": key, "var": "{{" + key + "}}",
                "source": name, "original": text[:50]}

    def analyze_dependencies(self) -> dict:
        """分析录制中的跨应用依赖，返回依赖报告"""
        apps = set()
        window_sequence = []
        for step in self.steps:
            if step.get("action") == "switch_window":
                window = step.get("window", "")
                if window:
                    apps.add(window)
                    window_sequence.append(window)
        return {
            "unique_apps": list(apps),
            "window_sequence": window_sequence,
            "app_count": len(apps),
        }