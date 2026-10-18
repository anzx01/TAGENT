"""Assemble registered step ranges into a reviewable task."""
import copy
import json
import re
from pathlib import Path

from services.workflow_plan import build_plan, plan_source_hash


def compose_task(data_dir: Path, entries: list[dict], capability_ids: list[str], name: str) -> dict:
    if not name.strip() or not capability_ids:
        raise ValueError("任务名称和至少一项能力必填")
    by_id = {entry["id"]: entry for entry in entries}
    steps = []
    sources = []
    var_sources = {}
    session_steps = []
    for cap_id in capability_ids:
        entry = by_id.get(cap_id)
        if not entry:
            raise ValueError(f"能力不存在: {cap_id}")
        if entry.get("steps"):
            original = entry["steps"]
            start, end = 0, len(original)
            part = copy.deepcopy(original)
            source_hash = entry.get("source_hash", "")
            meta = {"var_sources": entry.get("var_sources", {}),
                    "session_steps": entry.get("session_steps", [])}
        else:
            source = data_dir / entry["source_task"] / "task.json"
            try:
                task = json.loads(source.read_text(encoding="utf-8"))
                if entry.get("source_hash") and entry["source_hash"] != plan_source_hash(task):
                    raise ValueError("源任务已改变，请重新登记能力")
                original = task["steps"]
                start, end = entry["step_range"]
                if not (0 <= start < end <= len(original)):
                    raise ValueError("步骤区间已失效")
            except (OSError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"能力源任务不可用: {entry['name']}: {exc}") from exc
            part = copy.deepcopy(original[start:end])
            source_hash = plan_source_hash(task)
            meta_path = data_dir / entry["source_task"] / "meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        composed_start = len(steps)
        required_variables = {name for step in part
                              for name in re.findall(r"\{\{\s*(\w+)\s*\}\}",
                                                     str(step.get("text", "")))}
        for var_name, binding in meta.get("var_sources", {}).items():
            if var_name not in required_variables:
                continue
            if var_name in var_sources and var_sources[var_name] != binding:
                raise ValueError(f"变量 {var_name} 在不同能力中绑定了不同来源")
            var_sources[var_name] = binding
        session_steps.extend(len(steps) + index - start
                             for index in meta.get("session_steps", [])
                             if start <= index < end)
        for step in part:
            step["index"] = len(steps)
            step["capability"] = {"id": cap_id, "name": entry["name"]}
            if step.get("action") == "verify_capability":
                step["postcondition"] = copy.deepcopy(entry.get("postcondition") or step.get("postcondition") or {})
                step["capability_end"] = True
            steps.append(step)
        if not any(step.get("action") == "verify_capability" for step in part):
            steps.append({"index": len(steps), "action": "verify_capability",
                          "effect": "readonly", "window": part[-1].get("window", ""),
                          "capability_end": True,
                          "capability": {"id": cap_id, "name": entry["name"]},
                          "postcondition": copy.deepcopy(entry.get("postcondition") or {})})
        sources.append({"id": cap_id, "source_task": entry.get("source_task", ""),
                        "source_hash": source_hash, "step_range": [start, end],
                        "composed_range": [composed_start, len(steps)]})
    result = {"version": 2, "name": name.strip(), "steps": steps,
              "step_count": len(steps), "capability_sources": sources,
              "composed_meta": {"var_sources": var_sources,
                                "session_steps": session_steps}}
    result["plan"] = build_plan(result, session_steps=session_steps)
    return result
