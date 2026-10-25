"""有界用户记忆库(对标 Muse 可控记忆,mano-afk rules.md 100 条上限式)。

规则喂给 LLM 自愈/意图分析作偏好上下文;可查看、单条遗忘、清空。
存储 data/memory_rules.json;超出上限按最旧淘汰。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_RULES = 100


def store_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / "memory_rules.json"


def load(base_dir: Path) -> list[dict]:
    try:
        data = json.loads(store_path(base_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(base_dir: Path, rules: list[dict]) -> None:
    path = store_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def add(base_dir: Path, text: str, source: str = "user") -> dict:
    text = str(text or "").strip()
    if not text:
        raise ValueError("记忆内容为空")
    rules = load(base_dir)
    rule = {"id": f"m{int(time.time() * 1000) % 10**9}", "text": text[:200],
            "source": source, "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    rules.append(rule)
    save(base_dir, rules[-MAX_RULES:])  # 超限淘汰最旧
    return rule


def forget(base_dir: Path, rule_id: str) -> bool:
    rules = load(base_dir)
    kept = [r for r in rules if r.get("id") != rule_id]
    if len(kept) != len(rules):
        save(base_dir, kept)
        return True
    return False


def context(base_dir: Path, limit: int = 10) -> str:
    """给 LLM 的偏好上下文(最近 limit 条)。"""
    rules = load(base_dir)[-limit:]
    if not rules:
        return ""
    return "用户偏好规则:\n" + "\n".join(f"- {r['text']}" for r in rules)
