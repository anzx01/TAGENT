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
    """断点有效即可续跑(非 stale 且范围内);实际续跑 index 由 resolve_resume_index 对齐。"""
    saved = read_checkpoint(path, task)
    try:
        return (saved is not None and not saved["stale"]
                and 0 < int(requested) < len(task["steps"]))
    except (KeyError, TypeError, ValueError):
        return False


def resolve_resume_index(path: Path, task: dict, requested: int) -> int:
    """checkpoint 声明的续跑 index;按 step_id 对齐后可能与请求值不同。"""
    saved = read_checkpoint(path, task)
    want_id = (saved or {}).get("next_step_id", "")
    if want_id:
        for i, step in enumerate(task["steps"]):
            if step.get("step_id") == want_id:
                return i
    return int(requested)
