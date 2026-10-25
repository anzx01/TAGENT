"""意图分析层(移植 skill-recorder describer 的最小完整版)。

启发式优先:从 task.json 步骤确定性生成「意图 + 人读步骤」;LLM 只润色
title/intent/step 标题(metadata),按 step id 精确合并,失败静默降级
(移植 openskills ai/index.ts 的三重防御)。步骤本身永不来自 LLM。
"""
from __future__ import annotations

import json
import re

_ACTION_VERBS = {
    "click": "点击", "double_click": "双击", "right_click": "右键点击",
    "input_text": "输入", "key_press": "按键", "scroll": "滚动",
    "drag": "拖拽", "swipe": "滑动", "switch_window": "切换到窗口",
    "wait": "等待", "verify_capability": "核实能力",
}
# 只读/环境性动作为 calculation,改变世界的为 action(skill-recorder 的风险分层)
_CALC_ACTIONS = {"wait", "switch_window", "scroll", "verify_capability"}


def _sanitized(text: str, limit: int = 24) -> str:
    """脱敏:变量占位符保留,其余取长度不取内容,避免敏感值进入分析件。"""
    text = str(text or "")
    if re.fullmatch(r"\{\{\s*\w+\s*\}\}", text.strip()):
        return text.strip()
    if not text:
        return ""
    return f"{len(text)}字"


def heuristic_analysis(task: dict) -> dict:
    steps_out = []
    for i, step in enumerate(task.get("steps", []), start=1):
        action = str(step.get("action", ""))
        verb = _ACTION_VERBS.get(action, action)
        target = _sanitized(step.get("text") or step.get("key"))
        win = str(step.get("window", "") or "")
        detail = verb + (f" {target}" if target else "") + (f" (窗口: {win[:30]})" if win else "")
        steps_out.append({
            "id": f"s{i}",
            "title": detail[:60],
            "kind": "calculation" if action in _CALC_ACTIONS else "action",
        })
    windows = sorted({str(s.get("window", "")).split(" - ")[-1] for s in task.get("steps", [])
                      if s.get("window")})
    intent = "在 " + "、".join(windows[:3]) + " 中完成录制的一系列桌面操作" if windows \
        else "完成录制的一系列桌面操作"
    confidence = "medium" if steps_out else "low"
    return {
        "title": (task.get("name") or task.get("source") or "未命名任务")[:40],
        "intent": intent,
        "intentConfidence": confidence,
        "generalization": "录制是单次示例;含 {{变量}} 的步骤每次回放可换值,固定值如需变化请标注为变量。",
        "steps": steps_out,
        "generated_by": "heuristic",
    }


def merge_llm_polish(base: dict, polish: dict | None) -> dict:
    """按 id 精确合并 LLM 润色;LLM 不能增删步骤或改 kind。"""
    if not isinstance(polish, dict):
        return base
    out = dict(base)
    for key in ("title", "intent"):
        value = polish.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:120]
    by_id = {s["id"]: s for s in polish.get("steps", []) if isinstance(s, dict) and s.get("id")}
    out["steps"] = [{**s, "title": str(by_id[s["id"]].get("title", s["title"]))[:80]}
                    if s["id"] in by_id and str(by_id[s["id"]].get("title", "")).strip()
                    else s for s in base["steps"]]
    return out


def analyze(task: dict, llm_client=None) -> dict:
    """启发式分析 + 可选 LLM 润色(三重防御:白名单输入/精确合并/静默降级)。"""
    base = heuristic_analysis(task)
    if llm_client is None:
        return base
    try:
        prompt = ("为以下桌面自动化任务润色 title 与 intent(各一句话,中文),"
                  "并为每步写不超过 20 字的标题。只返回 JSON:"
                  '{"title","intent","steps":[{"id","title"}]},steps 的 id 必须原样保留。\n'
                  + json.dumps(base, ensure_ascii=False))
        polish = llm_client.ask_json(prompt)
        return merge_llm_polish(base, polish)
    except Exception:
        return base  # LLM 失败静默降级为纯启发式
