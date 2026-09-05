"""Portable Skill/Automation exports are generated from the plan, never executed."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.workflow_artifacts import export_artifact
from services.workflow_plan import build_plan


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        task = {"version": 2, "source": "demo", "steps": [
            {"index": 0, "action": "input_text", "text": "{{ order_id }}", "window": "Notepad", "effect": "mutating"},
        ]}
        plan = build_plan(task)
        task["plan"] = plan
        (root / "task.json").write_text(json.dumps(task), encoding="utf-8")
        (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (root / "review.json").write_text(json.dumps({
            "approved": False, "revision": plan["revision"],
            "source_hash": plan["source_hash"],
        }), encoding="utf-8")
        try:
            export_artifact(root, "skill")
        except PermissionError:
            pass
        else:
            raise AssertionError("unapproved workflow exported")
        (root / "review.json").write_text(json.dumps({
            "approved": True, "revision": plan["revision"],
            "source_hash": plan["source_hash"],
        }), encoding="utf-8")
        skill = export_artifact(root, "skill")
        automation = export_artifact(root, "automation")
        assert skill.exists() and "order_id" in skill.read_text(encoding="utf-8")
        payload = json.loads(automation.read_text(encoding="utf-8"))
        assert payload["trigger"]["type"] == "manual"
        assert payload["steps"][0]["text"] == "{{ order_id }}"
    print("workflow_artifacts PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
