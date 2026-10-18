"""Checkpoint validity follows the exact task source that was run."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.checkpoints import read_checkpoint, resume_step
from services.workflow_plan import plan_source_hash


def main():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "checkpoint.json"
        task = {"version": 2, "steps": [{"index": 0, "action": "click"},
                                        {"index": 1, "action": "key_press"}]}
        path.write_text(json.dumps({"next_step": 1, "source_hash": plan_source_hash(task)}), encoding="utf-8")
        saved = read_checkpoint(path, task)
        assert saved and not saved["stale"] and saved["next_step"] == 1
        assert resume_step(path, task, 1)
        assert not resume_step(path, task, 0)
        assert not resume_step(path, task, 2)
        task["steps"][0]["action"] = "delete"
        assert read_checkpoint(path, task)["stale"]
        assert not resume_step(path, task, 1)
        path.write_text("invalid", encoding="utf-8")
        assert read_checkpoint(path, task) is None
    print("checkpoints PASS")


if __name__ == "__main__":
    main()
