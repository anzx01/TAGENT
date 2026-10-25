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
        assert payload["trigger"]["natural_language"] == "手动触发"
        (root / "meta.json").write_text(json.dumps({"schedule": {
            "kind": "daily", "hour": 9, "minute": 30}}), encoding="utf-8")
        payload = json.loads(export_artifact(root, "automation").read_text(encoding="utf-8"))
        assert payload["trigger"] == {"type": "schedule", "kind": "daily", "hour": 9, "minute": 30,
                                      "natural_language": "每天 09:30"}
        (root / "meta.json").write_text(json.dumps({"schedule": {
            "kind": "interval", "minutes": 15, "next_run_at": 123}}), encoding="utf-8")
        payload = json.loads(export_artifact(root, "automation").read_text(encoding="utf-8"))
        assert payload["trigger"] == {"type": "schedule", "kind": "interval", "minutes": 15,
                                      "natural_language": "每 15 分钟"}
        # 已批准的意图分析进入 Skill.md;未批准不进
        skill_text = export_artifact(root, "skill").read_text(encoding="utf-8")
        assert skill_text.startswith("---") and "no API or CLI exists" in skill_text
        assert "## Capability boundaries" in skill_text
        (root / "analysis.json").write_text(json.dumps(
            {"title": "报销", "intent": "提交每日报销单", "approved": False}), encoding="utf-8")
        skill_text = export_artifact(root, "skill").read_text(encoding="utf-8")
        assert "提交每日报销单" not in skill_text
        analysis = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
        analysis["approved"] = True
        (root / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
        skill_text = export_artifact(root, "skill").read_text(encoding="utf-8")
        assert "提交每日报销单" in skill_text
        # 证据包 zip:文档 + 步骤截图
        (root / "screenshots").mkdir()
        (root / "screenshots" / "step1.jpg").write_bytes(b"x")
        bundle = export_artifact(root, "bundle")
        import zipfile
        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
        assert {"skill.md", "automation.json", "screenshots/step1.jpg"} <= names, names
    print("workflow_artifacts PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
