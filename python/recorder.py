"""录制引擎 MVP：捕获鼠标点击、键盘输入、截图、UI Automation 快照。"""

import json
import os
import time
import threading
from pathlib import Path

import mss
from pynput import mouse, keyboard
import uiautomation as ua


class Recorder:
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.screenshot_dir = self.output_dir / "screenshots"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self.events: list[dict] = []
        self.recording = False
        self._mouse_listener = None
        self._kb_listener = None
        self._lock = threading.Lock()
        self._sct = mss.mss()
        self._shot_index = 0
        self._start_time = 0.0

    def start(self):
        """开始录制。"""
        self.events = []
        self._shot_index = 0
        self._start_time = time.time()
        self.recording = True

        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._kb_listener = keyboard.Listener(on_press=self._on_key_press)
        self._mouse_listener.start()
        self._kb_listener.start()

    def stop(self) -> str:
        """停止录制并保存。返回保存路径。"""
        self.recording = False
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._kb_listener:
            self._kb_listener.stop()

        events_path = self.output_dir / "events.json"
        with open(events_path, "w", encoding="utf-8") as f:
            json.dump(
                {"events": self.events, "count": len(self.events)},
                f,
                ensure_ascii=False,
                indent=2,
            )
        return str(events_path)

    def _elapsed(self) -> float:
        return round(time.time() - self._start_time, 3)

    def _take_screenshot(self) -> str | None:
        """截屏并保存，返回文件名。"""
        try:
            filename = f"shot_{self._shot_index:04d}.png"
            self._shot_index += 1
            path = self.screenshot_dir / filename
            monitor = self._sct.monitors[1]
            img = self._sct.grab(monitor)
            mss.tools.to_png(img.rgb, img.size, output=str(path))
            return filename
        except Exception:
            return None

    def _grab_uia_snapshot(self) -> dict | None:
        """抓取当前焦点控件的 UI Automation 信息。"""
        try:
            ctrl = ua.GetForegroundControl()
            focused = ua.GetFocusedControl()
            return {
                "window": ctrl.Name[:200] if ctrl.Name else "",
                "window_class": ctrl.ClassName[:100] if ctrl.ClassName else "",
                "focused_control": focused.Name[:200] if focused.Name else "",
                "focused_type": focused.ControlTypeName if focused.ControlTypeName else "",
            }
        except Exception:
            return None

    def _on_click(self, x, y, button, pressed):
        if not self.recording or not pressed:
            return
        with self._lock:
            shot = self._take_screenshot()
            uia = self._grab_uia_snapshot()
            self.events.append(
                {
                    "type": "click",
                    "x": int(x),
                    "y": int(y),
                    "button": str(button),
                    "screenshot": shot,
                    "uia": uia,
                    "t": self._elapsed(),
                }
            )

    def _on_key_press(self, key):
        if not self.recording:
            return
        with self._lock:
            try:
                key_str = key.char
            except AttributeError:
                key_str = str(key)
            self.events.append(
                {
                    "type": "key",
                    "key": key_str,
                    "t": self._elapsed(),
                }
            )
