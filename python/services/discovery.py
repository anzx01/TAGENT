"""Local, read-only discovery of repetitive workflows.

Unlike a telemetry product, this module only consumes task metadata and local
run reports. It intentionally reports observed repetition rather than inventing
financial ROI from incomplete timing data.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

__all__ = ["rank_candidates"]


def rank_candidates(tasks: Iterable[dict[str, Any]], runs_by_task: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id", ""))
        runs = list(runs_by_task.get(task_id, []))
        run_count = len(runs)
        step_count = int(task.get("steps", 0) or 0)
        successes = sum(1 for run in runs if run.get("success"))
        pauses = sum(int(run.get("pause_count", 0) or 0) for run in runs)
        # Repetition is evidence-weighted: observed runs times executable
        # steps. It is a ranking signal, not a promise of savings.
        repeat_score = run_count * max(step_count, 1)
        candidates.append({
            "task_id": task_id,
            "name": task.get("name") or task_id,
            "steps": step_count,
            "runs": run_count,
            "successful_runs": successes,
            "success_rate": round(successes / run_count, 3) if run_count else None,
            "human_takeovers": pauses,
            "repeat_score": repeat_score,
            "evidence": [f"observed {run_count} runs", f"{step_count} executable steps"],
        })
    candidates.sort(key=lambda item: (item["repeat_score"], item["success_rate"] or 0), reverse=True)
    return candidates
