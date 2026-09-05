"""RunContext detaches a replay from mutable task files."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.run_context import build_run_context
from services.workflow_plan import build_plan


def main() -> int:
    task = {"version": 2, "steps": [{
        "index": 0, "action": "click", "window": "Notepad",
        "locator": [{"type": "uia", "name": "Open"}],
    }]}
    plan = build_plan(task, revision=4)
    ctx = build_run_context("20260905_000001", task, plan, from_step=0)
    steps = ctx.steps
    steps[0]["action"] = "delete"
    assert ctx.steps[0]["action"] == "click"
    assert ctx.plan_revision == 4
    assert ctx.source_hash == plan["source_hash"]
    try:
        build_run_context("x", {**task, "steps": [{"action": "delete"}]}, plan)
    except ValueError:
        pass
    else:
        raise AssertionError("stale plan accepted")
    print("run_context PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
