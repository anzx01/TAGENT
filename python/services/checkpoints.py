"""Read and validate a replay checkpoint against its current task source."""
import json
from pathlib import Path

from services.workflow_plan import plan_source_hash


def read_checkpoint(path: Path, task: dict) -> dict | None:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            return None
        saved["stale"] = saved.get("source_hash") != plan_source_hash(task)
        return saved
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def resume_step(path: Path, task: dict, requested: int) -> bool:
    saved = read_checkpoint(path, task)
    try:
        return (saved is not None and not saved["stale"]
                and 0 < int(requested) < len(task["steps"])
                and int(saved.get("next_step", -1)) == int(requested))
    except (KeyError, TypeError, ValueError):
        return False
