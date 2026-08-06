"""回放引擎 MVP：读取事件序列，按坐标回放。"""

import json
import time
from pathlib import Path

import pyautogui


class Player:
    def __init__(self, events_path: str):
        self.events_path = Path(events_path)
        with open(self.events_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.events = data.get("events", [])

    def play(self, step_delay: float = 0.5):
        """逐步回放事件。"""
        pyautogui.FAILSAFE = False
        prev_t = 0.0
        for evt in self.events:
            # 按录制时的时间间隔回放
            wait = max(0, evt["t"] - prev_t)
            time.sleep(min(wait, 3.0))  # 最多等3秒

            if evt["type"] == "click":
                x, y = evt["x"], evt["y"]
                btn = "right" if "right" in evt.get("button", "") else "left"
                pyautogui.click(x, y, button=btn)
            elif evt["type"] == "key":
                key = evt["key"]
                # 单字符直接输入
                if len(key) == 1:
                    pyautogui.press(key)
                else:
                    # 特殊键如 Key.space -> space
                    clean = key.replace("Key.", "")
                    try:
                        pyautogui.press(clean)
                    except Exception:
                        pass

            prev_t = evt["t"]
            time.sleep(step_delay)
