"""Caddi-style Automation Plan contract stays deterministic and side-effect free."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.workflow_plan import build_plan


def main() -> int:
    task = {"version": 2, "steps": [
        {"index": 0, "action": "input_text", "window": "Notepad", "effect": "mutating"},
        {"index": 1, "action": "click", "window": "", "effect": "mutating",
         "locator": [{"type": "screen_coord"}]},
        {"index": 2, "action": "click", "window": "Notepad", "effect": "destructive",
         "locator": [{"type": "uia"}]},
    ]}
    plan = build_plan(task)
    assert [s["mode"] for s in plan["steps"]] == ["deterministic", "agent_assist", "human_required"]
    assert plan["approval_required"]
    assert plan["open_questions"][0]["step"] == 1
    assert len(plan["source_hash"]) == 16
    print("workflow_plan PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
