"""golden-vs-buggy 回归基线(移植 Mano-P mano-afk benchmark 思想)。

成功回放自动存黄金基线(预期 PASS);之后每次运行与基线做确定性比对;
`degrade` 派生预期 FAIL 的退化变体(降级定位器),供人工验证"失败真的会失败"。
自愈/纠错改动后跑基线比对,即可回归检测是否引入副作用。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_SCORE_ACTIONS = {"click", "right_click", "double_click", "input_text",
                  "key_press", "scroll", "drag", "swipe"}


def _action_seq(report: dict) -> list[str]:
    return [s.get("action", "") for s in report.get("steps", [])
            if s.get("action") in _SCORE_ACTIONS]


def baseline_from_run(report: dict) -> dict:
    """成功运行 → 黄金基线(动作序列 + 通过的能力断言)。"""
    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_hash": report.get("source_hash", ""),
        "plan_revision": report.get("plan_revision"),
        "actions": _action_seq(report),
        "verified": [s.get("capability", {}).get("name", "")
                     for s in report.get("steps", [])
                     if s.get("verification") == "passed" and isinstance(s.get("capability"), dict)],
    }


def save(recording_dir: str | Path, report: dict) -> Path:
    root = Path(recording_dir)
    path = root / "baseline.json"
    path.write_text(json.dumps(baseline_from_run(report), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def compare(recording_dir: str | Path, report: dict) -> dict:
    """与基线比对:动作序列须为基线的有序超集(允许新增,不允许缺失/错序)。"""
    path = Path(recording_dir) / "baseline.json"
    if not path.exists():
        return {"checked": False}
    try:
        base = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"checked": False}
    want, got = list(base.get("actions", [])), _action_seq(report)
    it = iter(got)
    matched = all(any(w == g for g in it) for w in want)
    return {
        "checked": True,
        "pass": bool(matched and report.get("success")),
        "missing": [w for w in want if w not in got],
        "baseline_revision": base.get("plan_revision"),
    }


def degrade(task: dict) -> dict:
    """生成预期失败的退化变体:每个定位链只留置信度最低的候选,坐标偏移。"""
    out = json.loads(json.dumps(task))
    for step in out.get("steps", []):
        locators = step.get("locator") or []
        if len(locators) > 1:
            step["locator"] = [locators[-1]]
        for loc in step.get("locator", []):
            if loc.get("type") in ("coord", "screen_coord"):
                loc["x"] = int(loc.get("x", 0)) + 20000
                loc["y"] = int(loc.get("y", 0)) + 20000
            if "client_pct_x" in loc:
                loc["client_pct_x"] = (float(loc["client_pct_x"]) + 90) % 100
                loc["client_pct_y"] = (float(loc["client_pct_y"]) + 90) % 100
    out["name"] = str(out.get("name", "")) + " (buggy-variant)"
    out["expected_verdict"] = "fail"
    return out
