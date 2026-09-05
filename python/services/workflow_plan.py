"""Build a reviewable, deterministic execution plan from a compiled task.

This is the local equivalent of Caddi's Automation Plan: it is deliberately
pure and does not execute actions or call an LLM.  The player can continue to
consume the legacy ``steps`` array while the UI and future bounded-agent layer
use this contract to explain risk and open questions first.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

__all__ = ["build_plan", "plan_source_hash"]


def plan_source_hash(task: dict[str, Any]) -> str:
    """Return a stable hash for the executable step source."""
    source = {"version": task.get("version"), "steps": task.get("steps", [])}
    raw = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _locator_types(step: dict[str, Any]) -> list[str]:
    return [str(item.get("type")) for item in step.get("locator", []) if item.get("type")]


def _step_mode(step: dict[str, Any], session_steps: set[int]) -> tuple[str, str | None]:
    index = int(step.get("index", -1))
    action = str(step.get("action", ""))
    effect = str(step.get("effect", "readonly"))
    locators = _locator_types(step)
    if index in session_steps or effect == "destructive":
        return "human_required", "登录/验证或破坏性操作需要人工确认"
    if action in {"wait", "switch_window"}:
        return "deterministic", None
    if not locators:
        # Text/keyboard steps can still be deterministic when their target
        # window is known; otherwise the plan must ask the user to clarify it.
        if step.get("window"):
            return "deterministic", None
        return "human_required", "步骤没有目标窗口或定位证据"
    if set(locators).issubset({"ocr", "screen_coord"}):
        return "agent_assist", "定位依赖 OCR/屏幕坐标，运行前建议确认目标"
    return "deterministic", None


def build_plan(task: dict[str, Any], *, session_steps: list[int] | None = None,
               revision: int = 1) -> dict[str, Any]:
    """Create a stable plan summary suitable for UI review and persistence."""
    session = {int(value) for value in (session_steps or [])}
    steps: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    variables: list[str] = []
    seen_variables: set[str] = set()
    side_effects: list[dict[str, Any]] = []
    for raw in task.get("steps", []):
        step = dict(raw)
        mode, question = _step_mode(step, session)
        item = {
            "index": int(step.get("index", len(steps))),
            "action": step.get("action", ""),
            "mode": mode,
            "effect": step.get("effect", "readonly"),
            "window": step.get("window", ""),
            "locator_types": _locator_types(step),
            "locator_evidence": [
                {k: v for k, v in loc.items()
                 if k in {"type", "name", "control_type", "class", "auto_id",
                          "selector", "confidence", "window"}}
                for loc in step.get("locator", []) if isinstance(loc, dict)
            ],
            "text_preview": str(step.get("text", step.get("key", "")))[:80],
        }
        steps.append(item)
        for name in re.findall(r"\{\{\s*(\w+)\s*\}\}", str(step.get("text", ""))):
            if name not in seen_variables:
                seen_variables.add(name)
                variables.append(name)
        if item["effect"] in {"mutating", "destructive"}:
            side_effects.append({
                "step": item["index"], "effect": item["effect"],
                "action": item["action"], "window": item["window"],
            })
        if question:
            questions.append({"step": item["index"], "question": question})
    source_hash = plan_source_hash(task)
    return {
        "version": 1,
        "revision": int(revision),
        "plan_revision": int(revision),
        "source_hash": source_hash,
        "steps": steps,
        "variables": variables,
        "side_effects": side_effects,
        "intent": {"source_task": task.get("source", ""), "step_count": len(steps)},
        "open_questions": questions,
        "approval_required": bool(questions),
        "execution_policy": "deterministic_first_bounded_assist",
    }
