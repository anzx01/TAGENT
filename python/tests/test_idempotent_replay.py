"""Regression coverage for already-closed desktop windows."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import Player


def main() -> int:
    close_click = {
        "action": "click",
        "window": "Example - Visual Studio Code",
        "locator": [{"type": "coord", "client_pct_x": 93.6, "client_pct_y": 0.9}],
    }
    ordinary_click = {
        "action": "click",
        "window": "Example - Visual Studio Code",
        "locator": [{"type": "coord", "client_pct_x": 50, "client_pct_y": 50}],
    }
    desktop_click = {
        "action": "click",
        "window": "",
        "locator": [{"type": "screen_coord", "x": 100, "y": 100}],
    }
    assert Player._is_idempotent_missing_window(close_click)
    assert not Player._is_idempotent_missing_window(ordinary_click)
    assert not Player._is_idempotent_missing_window(desktop_click)

    # 同窗口多段输入的覆写判定：后段不得把前段清掉（保真），
    # 但上一轮残留/空字段仍覆写（幂等）。
    p = Player.__new__(Player)
    p._typed_windows = set()
    p._field_text = lambda hwnd: None
    step = {"action": "input_text", "text": "B"}
    assert p._should_overwrite(1, "B", step)                     # 首次输入：覆写
    p._typed_windows.add(1)
    assert p._should_overwrite(1, "B", step)                      # 字段读不到：保守覆写
    p._field_text = lambda hwnd: ""
    assert p._should_overwrite(1, "B", step)                      # 空字段：覆写
    p._field_text = lambda hwnd: "B"
    assert p._should_overwrite(1, "B", step)                      # 上轮残留==本步文本：覆写
    p._field_text = lambda hwnd: "A\r\n"
    assert not p._should_overwrite(1, "B", step)                  # 本轮多段输入：追加
    assert not p._should_overwrite(1, "B", {"replace": False})    # 显式禁用覆写
    print("idempotent_replay PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
