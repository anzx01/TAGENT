"""Regression coverage for physical desktop target validation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player


def main() -> int:
    original = {name: getattr(player.win32gui, name) for name in ("WindowFromPoint", "GetClassName", "GetParent")}
    try:
        player.win32gui.WindowFromPoint = lambda point: 10
        player.win32gui.GetClassName = lambda hwnd: "SHELLDLL_DefView" if hwnd == 10 else ""
        player.win32gui.GetParent = lambda hwnd: 0
        assert player._desktop_window_at(100, 100)

        player.win32gui.GetClassName = lambda hwnd: "#32768"
        assert not player._desktop_window_at(100, 100)
        assert player._desktop_window_at(100, 100, allow_menu=True)

        # A recorded desktop workflow may finish by clicking a taskbar
        # button to return to the controller.  The taskbar is a shell-owned
        # target, but it must not be confused with an arbitrary application
        # window.
        player.win32gui.GetClassName = lambda hwnd: "Shell_TrayWnd"
        assert player._desktop_window_at(100, 100)

        player.win32gui.WindowFromPoint = lambda point: 0
        assert not player._desktop_window_at(100, 100)
    finally:
        for name, value in original.items():
            setattr(player.win32gui, name, value)
    print("desktop_target PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
