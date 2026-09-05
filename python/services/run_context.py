"""Immutable replay context and provenance helpers.

The executable task on disk is a mutable user artifact (variables, reviewed
patches and plans may change between runs).  A replay therefore receives a
detached snapshot with the exact plan revision and source hash that was
reviewed at start time.  The snapshot is intentionally JSON-backed so callers
cannot mutate the running step list through a shared reference.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from services.workflow_plan import plan_source_hash

__all__ = ["RunContext", "build_run_context"]


@dataclass(frozen=True)
class RunContext:
    """Read-only provenance for one replay invocation."""

    recording_id: str
    plan_revision: int
    source_hash: str
    started_at: float
    from_step: int
    trigger: str
    review_approved: bool
    _steps_json: str

    @property
    def steps(self) -> list[dict[str, Any]]:
        """Return a fresh step snapshot on every access."""
        value = json.loads(self._steps_json)
        return value if isinstance(value, list) else []

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "plan_revision": self.plan_revision,
            "source_hash": self.source_hash,
            "started_at": self.started_at,
            "from_step": self.from_step,
            "trigger": self.trigger,
            "review_approved": self.review_approved,
            "draft": not self.review_approved,
        }


def build_run_context(
    recording_id: str,
    task: dict[str, Any],
    plan: dict[str, Any] | None = None,
    *,
    from_step: int = 0,
    trigger: str = "manual",
    review: dict[str, Any] | None = None,
) -> RunContext:
    """Validate plan provenance and detach executable steps for a run.

    A stale/malformed plan is rejected rather than silently executing a task
    whose review no longer covers its current steps.
    """
    steps = task.get("steps", [])
    if not isinstance(steps, list) or not steps:
        raise ValueError("task has no executable steps")
    expected_hash = plan_source_hash(task)
    plan = plan or task.get("plan") or {}
    source_hash = str(plan.get("source_hash") or "")
    if source_hash and source_hash != expected_hash:
        raise ValueError("plan source_hash does not match task steps")
    # Legacy task files may have no plan metadata; synthesize a provenance
    # record while preserving compatibility. New files always persist these
    # fields during compilation.
    revision = int(plan.get("revision", task.get("plan_revision", 1)) or 1)
    review = review or {}
    review_hash = review.get("source_hash")
    review_revision = review.get("revision", review.get("plan_revision"))
    # A review from another source/revision is not proof of approval. Keep the
    # run possible as an explicitly marked draft, but never report it as a
    # reviewed execution.
    review_approved = bool(review.get("approved", False)) and (
        str(review_hash or "") == (source_hash or expected_hash)
        and review_revision is not None
        and int(review_revision) == revision
    )
    return RunContext(
        recording_id=str(recording_id),
        plan_revision=revision,
        source_hash=source_hash or expected_hash,
        started_at=time.time(),
        from_step=max(0, int(from_step or 0)),
        trigger=str(trigger or "manual"),
        review_approved=review_approved,
        _steps_json=json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
