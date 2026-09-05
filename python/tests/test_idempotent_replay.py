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
    print("idempotent_replay PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
