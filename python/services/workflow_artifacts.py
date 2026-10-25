"""Export reviewed workflow plans as portable local artifacts.

The artifacts mirror the useful boundary in Skill Recorder/Caddi: recording
evidence stays local, while the user-facing Skill/Automation is generated from
an explicit plan. Export is opt-in and never runs the workflow.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from services.workflow_plan import build_plan, plan_source_hash

__all__ = ["export_artifact"]


def _variables(task: dict[str, Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for step in task.get("steps", []):
        text = str(step.get("text", ""))
        for name in re.findall(r"\{\{\s*(\w+)\s*\}\}", text):
            if name not in seen:
                seen.add(name)
                found.append(name)
    return found


def _label(task: dict[str, Any]) -> str:
    return str(task.get("name") or task.get("source") or "TAGENT workflow").strip()


def _skill_markdown(task: dict[str, Any], plan: dict[str, Any],
                    analysis: dict[str, Any] | None = None) -> str:
    analysis = analysis or {}
    lines = [
        "---",
        f"name: {__import__('re').sub(r'[^a-z0-9-]+', '-', _label(task).lower()).strip('-') or 'tagent-workflow'}",
        'description: "Use when the user asks to repeat this recorded desktop workflow '
        'and no API or CLI exists for the target app."',
        "---",
        "",
        f"# {_label(task)}",
        "",
        "> Generated from an approved TAGENT workflow plan. Review before installing or sharing.",
        "",
        "## Intent",
        "",
        analysis.get("intent")
        or f"Repeat the recorded workflow `{task.get('source', '')}` using deterministic desktop/browser actions.",
        "",
    ]
    if analysis.get("generalization"):
        lines.extend([f"> 泛化提示: {analysis['generalization']}", ""])
    variables = _variables(task)
    lines.extend(["## Inputs", ""])
    lines.extend([f"- `{name}`" for name in variables] or ["- None declared"])
    lines.extend(["", "## Procedure", ""])
    for step in plan.get("steps", []):
        detail = f"{step['action']}"
        if step.get("text_preview"):
            detail += f" `{step['text_preview']}`"
        if step.get("window"):
            detail += f" in `{step['window']}`"
        lines.append(f"{step['index'] + 1}. **[{step['mode']}]** {detail}")
    lines.extend([
        "", "## Capability boundaries", "",
        "- 窗口位置/分辨率大幅变化时,百分比定位可能漂移,步骤会暂停等待人工而非误点。",
        "- 无法越过操作系统级权限/登录对话框;这类步骤在 TAGENT 中标记为会话步骤,需人工完成。",
        "- 单会话单任务串行执行;不支持后台并行驱动同一应用。",
        "", "## Safety", "",
        "- Deterministic steps run first; agent assistance may propose a repair but does not change this procedure during a run.",
        "- Human-required steps and destructive actions must be confirmed in TAGENT.",
        f"- Plan source hash: `{plan.get('source_hash', '')}`",
        "",
    ])
    return "\n".join(lines)


def _natural_language(schedule: dict | None) -> str:
    if not schedule:
        return "手动触发"
    if schedule.get("kind") == "daily":
        return f"每天 {int(schedule.get('hour', 9)):02d}:{int(schedule.get('minute', 0)):02d}"
    if schedule.get("kind") == "interval":
        return f"每 {int(schedule.get('minutes', 60))} 分钟"
    return "手动触发"


def _automation(task: dict[str, Any], plan: dict[str, Any], schedule: dict | None = None) -> dict[str, Any]:
    trigger = {"type": "manual", "natural_language": _natural_language(None)}
    if schedule and schedule.get("kind") == "daily":
        trigger = {"type": "schedule", "kind": "daily",
                   "hour": schedule["hour"], "minute": schedule["minute"],
                   "natural_language": _natural_language(schedule)}
    elif schedule and schedule.get("kind") == "interval":
        trigger = {"type": "schedule", "kind": "interval",
                   "minutes": schedule["minutes"],
                   "natural_language": _natural_language(schedule)}
    return {
        "version": 1,
        "name": _label(task),
        "source_task": task.get("source", ""),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "trigger": trigger,
        "execution_policy": plan.get("execution_policy", "deterministic_first_bounded_assist"),
        "plan_revision": plan.get("revision", 1),
        "source_hash": plan.get("source_hash", ""),
        "open_questions": plan.get("open_questions", []),
        "steps": task.get("steps", []),
    }


def export_artifact(recording_dir: str | Path, kind: str) -> Path:
    """Write one portable artifact and return its path."""
    root = Path(recording_dir)
    task_path = root / "task.json"
    if not task_path.exists():
        raise FileNotFoundError("task.json not found")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    plan_path = root / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    else:
        plan = task.get("plan") or build_plan(task)
    expected_hash = plan_source_hash(task)
    if plan.get("source_hash") != expected_hash:
        raise PermissionError("workflow plan is stale; recompile before export")
    review_path = root / "review.json"
    if not review_path.exists():
        raise PermissionError("workflow plan has not been reviewed")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if not bool(review.get("approved", False)):
        raise PermissionError("workflow plan is not approved; review open questions before export")
    review_revision = review.get("revision", review.get("plan_revision"))
    if review_revision is None or int(review_revision) != int(plan.get("revision", 1)):
        raise PermissionError("review does not match the current plan revision")
    review_hash = review.get("source_hash")
    if review_hash is None or str(review_hash) != expected_hash:
        raise PermissionError("review does not match the current plan source")
    if kind == "skill":
        analysis = _load_analysis(root)
        path = root / "skill.md"
        path.write_text(_skill_markdown(task, plan, analysis), encoding="utf-8")
    elif kind == "automation":
        path = root / "automation.json"
        meta_path = root / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        path.write_text(json.dumps(_automation(task, plan, meta.get("schedule")), ensure_ascii=False, indent=2), encoding="utf-8")
    elif kind == "bundle":
        # 证据包:skill.md + automation.json + 步骤截图,一站式分发(openskills export zip)
        import zipfile
        export_artifact(root, "skill")
        export_artifact(root, "automation")
        path = root / "bundle.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(root / "skill.md", "skill.md")
            zf.write(root / "automation.json", "automation.json")
            shots = root / "screenshots"
            if shots.is_dir():
                for shot in sorted(shots.glob("*.jpg")) + sorted(shots.glob("*.png")):
                    zf.write(shot, f"screenshots/{shot.name}")
        return path
    else:
        raise ValueError("kind must be skill, automation or bundle")
    return path


def _load_analysis(root: Path) -> dict[str, Any] | None:
    """已批准的意图分析进入导出件;未分析/未批准则用默认描述。"""
    path = root / "analysis.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data.get("approved") else None
    except (OSError, ValueError):
        return None
