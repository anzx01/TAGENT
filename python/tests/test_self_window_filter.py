"""Regression test: TAGENT's own browser UI must not enter replay tasks."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compiler import Compiler


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        steps = [
            {"action": "click", "window": "\u667a\u80fd\u64cd\u4f5c\u52a9\u624b - Brave", "screen_x": 10, "screen_y": 10},
            {"action": "click", "window": "User App", "screen_x": 20, "screen_y": 20},
        ]
        (root / "events.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")
        Compiler(root).compile()
        task = json.loads((root / "task.json").read_text(encoding="utf-8"))
        assert [s["window"] for s in task["steps"]] == ["User App"]
    print("self_window_filter PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
