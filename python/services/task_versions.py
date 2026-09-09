"""
task.json 版本化（移植 openskills-recorder 的不可变版本快照 + skill-diff 结构化对比）

- 每次 task.json 变更（编译/合并/人工编辑）写 versions/v{N}/task.json 不可变快照
- versions/manifest.json 记录 current 指针与变更历史（reason/ts/step_count/source_hash）
- 内容与当前版本语义一致时跳过快照（幂等，重编译不产生空版本）
- diff_tasks 输出结构化条目（field/type: added|removed|changed），用于
  合并预览、纠错审计和版本对比 UI

目录约定：
  <rec>/task.json                 # 工作副本（player 仍只读它，行为不变）
  <rec>/versions/v{N}/task.json   # 不可变快照
  <rec>/versions/manifest.json    # {current: N, history: [...]}
"""
import json
import time
from pathlib import Path

__all__ = ["TaskVersions", "diff_tasks"]

# 合并/重编译会重排时间戳与索引，不参与语义对比
_VOLATILE_STEP_KEYS = {"t", "index"}
# 对比时忽略的派生字段（每次编译都会变，不构成版本差异）
_VOLATILE_TASK_KEYS = {"plan"}


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _strip_step(step: dict) -> dict:
    return {k: v for k, v in step.items() if k not in _VOLATILE_STEP_KEYS}


def _strip_task(task: dict) -> dict:
    out = {k: v for k, v in task.items() if k not in _VOLATILE_TASK_KEYS}
    if isinstance(out.get("steps"), list):
        out["steps"] = [_strip_step(s) if isinstance(s, dict) else s
                        for s in out["steps"]]
    return out


def _summarize_step(step: dict) -> dict:
    """diff 条目里的步骤摘要（人可读，不携带全量定位证据）"""
    out = {"action": step.get("action")}
    for key in ("text", "key", "window"):
        if step.get(key):
            out[key] = step[key]
    if step.get("locator"):
        first = step["locator"][0]
        out["locator"] = f"{first.get('type', '')}:{first.get('name') or first.get('text') or ''}"
    if step.get("effect"):
        out["effect"] = step["effect"]
    return out


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def diff_tasks(old: dict, new: dict) -> list[dict]:
    """结构化 diff：标量字段 + 步骤按位置三路比对"""
    entries: list[dict] = []
    for field in ("step_count", "segment_count", "warning", "plan_revision"):
        a, b = old.get(field), new.get(field)
        if a != b:
            entries.append({"field": field, "type": "changed", "before": a, "after": b})
    old_steps = old.get("steps", [])
    new_steps = new.get("steps", [])
    for i in range(max(len(old_steps), len(new_steps))):
        a = _strip_step(old_steps[i]) if i < len(old_steps) else None
        b = _strip_step(new_steps[i]) if i < len(new_steps) else None
        label = f"steps[{i}]"
        if a is None:
            entries.append({"field": label, "type": "added",
                            "after": _summarize_step(new_steps[i])})
        elif b is None:
            entries.append({"field": label, "type": "removed",
                            "before": _summarize_step(old_steps[i])})
        elif _canon(a) != _canon(b):
            entries.append({"field": label, "type": "changed",
                            "before": _summarize_step(old_steps[i]),
                            "after": _summarize_step(new_steps[i])})
    return entries


class TaskVersions:
    def __init__(self, rec_dir: str | Path):
        self.rec_dir = Path(rec_dir)
        self.root = self.rec_dir / "versions"

    # ---------- 读取 ----------

    def manifest(self) -> dict:
        try:
            data = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
            if isinstance(data, dict) and "history" in data:
                return data
        except (OSError, ValueError):
            pass
        return {"current": 0, "history": []}

    def load(self, n: int) -> dict | None:
        p = self.root / f"v{n}" / "task.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def diff_vs_current(self, n: int) -> list[dict] | None:
        """历史版本 n 与当前最新版本的 diff"""
        mf = self.manifest()
        cur = int(mf.get("current", 0))
        if cur <= 0 or n == cur or n <= 0:
            return None
        old, new = self.load(n), self.load(cur)
        if old is None or new is None:
            return None
        return diff_tasks(old, new)

    # ---------- 写入 ----------

    def snapshot(self, task: dict, reason: str) -> dict | None:
        """写不可变快照；与当前版本语义一致时返回 None（幂等）"""
        mf = self.manifest()
        cur = int(mf.get("current", 0))
        if cur > 0:
            prev = self.load(cur)
            if prev is not None and _canon(_strip_task(prev)) == _canon(_strip_task(task)):
                return None
        n = cur + 1
        _atomic_json(self.root / f"v{n}" / "task.json", task)
        plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
        entry = {
            "n": n,
            "reason": reason,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "step_count": len(task.get("steps", [])),
            "source_hash": task.get("source_hash") or (plan or {}).get("source_hash", ""),
        }
        mf["current"] = n
        mf.setdefault("history", []).append(entry)
        _atomic_json(self.root / "manifest.json", mf)
        return entry
