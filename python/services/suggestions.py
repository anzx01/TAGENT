"""主动建议(对标 Muse unprompted suggestions 的本地版)。

挖运行历史:近 14 天失败 >=2 次的任务,聚出最常失败的步骤,
建议"重录该段"。只读、确定性、无 LLM。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

WINDOW_DAYS = 14
MIN_FAILURES = 2


def mine(runs_dir: str | Path, now: float | None = None, max_items: int = 5) -> list[dict]:
    now = now if now is not None else time.time()
    failures: dict[str, list[dict]] = {}
    for path in sorted(Path(runs_dir).glob("*.json")):
        if path.name.endswith(".context.json"):
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict) or report.get("success"):
            continue
        ts = report.get("finished_at") or 0
        if (now - float(ts)) > WINDOW_DAYS * 86400:
            continue
        rec_id = report.get("rec_id", "")
        if rec_id:
            failures.setdefault(rec_id, []).append(report)
    out: list[dict] = []
    for rec_id, reports in sorted(failures.items(), key=lambda kv: -len(kv[1])):
        if len(reports) < MIN_FAILURES:
            continue
        step_hits: dict[int, int] = {}
        for report in reports:
            failed = next((s.get("step") for s in report.get("steps", [])
                           if isinstance(s, dict) and s.get("status") not in ("ok", "skipped")), None)
            if failed is not None:
                step_hits[int(failed)] = step_hits.get(int(failed), 0) + 1
        if not step_hits:
            continue
        worst_step = max(step_hits, key=step_hits.get)
        out.append({"rec_id": rec_id, "failures": len(reports), "step": worst_step,
                    "message": f"近 {WINDOW_DAYS} 天失败 {len(reports)} 次,最常卡在第 {worst_step + 1} 步,建议重录该段"})
        if len(out) >= max_items:
            break
    return out
