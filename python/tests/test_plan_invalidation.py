"""Task edits create a new plan revision and revoke approval."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.task_store import TaskStore
from services.workflow_plan import build_plan


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "20260905_000001"
        root.mkdir(parents=True)
        task = {"version": 2, "steps": [{
            "index": 0, "action": "input_text", "text": "old",
            "window": "Notepad", "effect": "mutating",
        }]}
        plan = build_plan(task)
        task["plan"] = plan
        (root / "task.json").write_text(json.dumps(task), encoding="utf-8")
        (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (root / "review.json").write_text(json.dumps({
            "approved": True, "revision": 1, "source_hash": plan["source_hash"]
        }), encoding="utf-8")
        store = TaskStore(Path(td))
        assert store.set_variable("20260905_000001", 0, "order_id")
        new_task = json.loads((root / "task.json").read_text(encoding="utf-8"))
        new_plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
        review = json.loads((root / "review.json").read_text(encoding="utf-8"))
        assert new_task["steps"][0]["text"] == "{{ order_id }}"
        assert new_plan["revision"] == 2
        assert not review["approved"]
        assert review["source_hash"] == new_plan["source_hash"]
        assert store.set_variable("20260905_000001", 0, "")
        restored = json.loads((root / "task.json").read_text(encoding="utf-8"))
        assert restored["steps"][0]["text"] == "old"
    print("plan_invalidation PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
