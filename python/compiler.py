"""
编译器 - 将录制数据编译为带定位策略的 task.json
定位策略优先级：
  1. UI Automation 锚点（控件名+类型+AutomationId）
  2. 窗口内百分比 + 窗口标题（窗口移动/分辨率变化后仍有效）
  3. 屏幕百分比坐标兜底
"""
import json
from pathlib import Path


class Compiler:
    def __init__(self, recording_dir):
        self.dir = Path(recording_dir)
        events_path = self.dir / "events.json"
        with open(events_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.steps = data.get("steps", [])

    def compile(self):
        """编译录制数据为 task.json"""
        task_steps = []
        for i, step in enumerate(self.steps):
            task_steps.append(self._compile_step(step, i))

        task = {
            "version": 1,
            "source": self.dir.name,
            "step_count": len(task_steps),
            "steps": task_steps,
        }

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
        for key in ("text", "key", "delta", "end_x", "end_y", "duration_us"):
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
        """构建定位策略链：UIA > DOM > 坐标百分比 > OCR > 屏幕坐标"""
        strategies = []
        uia = step.get("uia")

        # 策略1: UI Automation 锚点
        if uia and (uia.get("name") or uia.get("auto_id") or uia.get("class")):
            strat = {
                "type": "uia",
                "name": uia.get("name", ""),
                "control_type": uia.get("type", ""),
                "class": uia.get("class", ""),
                "auto_id": uia.get("auto_id", ""),
            }
            if "parents" in uia:
                strat["parents"] = uia["parents"]
            if "sibling_index" in uia:
                strat["sibling_index"] = uia["sibling_index"]
            strategies.append(strat)

            # 策略2: DOM 定位（仅浏览器窗口，阶段2.2）
            window_title = step.get("window", "")
            from services.dom_locator import is_browser_window, build_dom_locator
            if is_browser_window(window_title):
                dom_loc = build_dom_locator(uia)
                if dom_loc:
                    strategies.append(dom_loc)

        # 策略3: 窗口内百分比 + 窗口标题
        if "client_x" in step and "client_y" in step and step.get("win_size"):
            w, h = step["win_size"]
            if w > 0 and h > 0:
                strategies.append({
                    "type": "coord",
                    "client_pct_x": round(step["client_x"] / w * 100, 1),
                    "client_pct_y": round(step["client_y"] / h * 100, 1),
                    "window": step.get("window", ""),
                })

        # 策略4: 屏幕百分比坐标
        if "pct_x" in step and "pct_y" in step:
            strategies.append({
                "type": "coord",
                "pct_x": step["pct_x"],
                "pct_y": step["pct_y"],
                "window": step.get("window", ""),
            })

        # 策略5: OCR 文本兜底（阶段1.1）
        if uia and uia.get("name"):
            strategies.append({
                "type": "ocr",
                "text": uia.get("name", ""),
                "window": step.get("window", ""),
            })

        # 策略6: 纯坐标兜底
        if "screen_x" in step:
            strategies.append({
                "type": "screen_coord",
                "x": step["screen_x"],
                "y": step["screen_y"],
            })

        return strategies