"""
任务库 - 在录制目录之上提供"任务"概念：
- 变量标注（阶段2.1）：步骤文本中的 {{var}} 占位符
- 定时调度配置（阶段4.2）
- 会话步骤标记（阶段2.4）
以 task.json + meta 注释持久化，不引入新存储。
"""
import json
from pathlib import Path

from services.workflow_plan import build_plan, plan_source_hash


class TaskStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def task_path(self, rec_id: str) -> Path:
        return self.data_dir / rec_id / "task.json"

    def meta_path(self, rec_id: str) -> Path:
        return self.data_dir / rec_id / "meta.json"

    def load_meta(self, rec_id: str) -> dict:
        p = self.meta_path(rec_id)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save_meta(self, rec_id: str, meta: dict):
        p = self.meta_path(rec_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _atomic_json(path: Path, value: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _refresh_plan_after_edit(self, rec_id: str, task: dict, reason: str):
        """Create a new plan revision and revoke prior approval after edits."""
        root = self.data_dir / rec_id
        old_plan = {}
        plan_path = root / "plan.json"
        if plan_path.exists():
            try:
                old_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old_plan = {}
        revision = int(old_plan.get("revision", task.get("plan_revision", 1)) or 1) + 1
        meta = self.load_meta(rec_id)
        plan = build_plan(task, session_steps=meta.get("session_steps", []), revision=revision)
        task["plan"] = plan
        task["plan_revision"] = revision
        task["source_hash"] = plan_source_hash(task)
        task_path = self.task_path(rec_id)
        self._atomic_json(task_path, task)
        # 版本化快照：人工编辑（变量标注/会话标记等）产生新版本，便于审计与回滚
        try:
            from services.task_versions import TaskVersions
            TaskVersions(root).snapshot(task, reason)
        except Exception:
            pass
        self._atomic_json(plan_path, plan)
        bundle_path = root / "bundle.json"
        bundle = {}
        if bundle_path.exists():
            try:
                bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bundle = {}
        bundle.update({
            "version": int(bundle.get("version", 1) or 1),
            "source": rec_id,
            "source_hash": plan["source_hash"],
            "revision": revision,
            "steps": task.get("steps", []),
        })
        bundle.setdefault("evidence", {"events": "events.jsonl", "screenshots": "screenshots/"})
        self._atomic_json(bundle_path, bundle)
        review_path = root / "review.json"
        review = {}
        if review_path.exists():
            try:
                review = json.loads(review_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                review = {}
        review.update({
            "approved": False,
            "revision": revision,
            "plan_revision": revision,
            "source_hash": plan["source_hash"],
            "open_questions": plan.get("open_questions", []),
            "updated_at": __import__("time").time(),
        })
        review.setdefault("history", []).append({
            "action": "invalidate", "reason": reason,
            "revision": revision, "source_hash": plan["source_hash"],
            "ts": __import__("time").time(),
        })
        self._atomic_json(review_path, review)
        return plan

    # ---------- 阶段2.1：变量标注 ----------

    def variables(self, rec_id: str) -> list[str]:
        """扫描 task 步骤文本中的 {{var}} 占位符，返回变量名列表"""
        return [item["name"] for item in self.variables_with_examples(rec_id)]

    def variables_with_examples(self, rec_id: str) -> list[dict]:
        """变量名 + 录制时的示例值(来自 _raw_text),供回放表单预填
        (移植 openskills parameters[].example 一键回放闭环)。"""
        p = self.task_path(rec_id)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        import re
        out: list[dict] = []
        seen: set[str] = set()
        for s in data.get("steps", []):
            m = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", str(s.get("text", "")).strip())
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                out.append({"name": m.group(1),
                            "example": str(s.get("_raw_text") or "")})
        return out

    def set_variable(self, rec_id: str, step_index: int, var_name: str) -> bool:
        """把某一步的文本替换为 {{var}} 占位符；var_name 为空则还原为原文本"""
        p = self.task_path(rec_id)
        if not p.exists():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        steps = data.get("steps", [])
        if step_index < 0 or step_index >= len(steps):
            return False
        step = steps[step_index]
        if step.get("action") != "input_text":
            return False
        raw = step.get("_raw_text")
        if raw is None:
            raw = step.get("text", "")
            step["_raw_text"] = raw
        step["text"] = f"{{{{ {var_name} }}}}" if var_name else raw
        self._refresh_plan_after_edit(rec_id, data, "variable_changed")
        return True

    # ---------- 阶段2.1：变量来源绑定 ----------

    def get_var_sources(self, rec_id: str) -> dict:
        """获取变量来源定义: {var_name: {type, ...}}"""
        meta = self.load_meta(rec_id)
        return meta.get("var_sources", {})

    def set_var_source(self, rec_id: str, var_name: str, source: dict | None):
        """设置变量来源。source: {type: "clipboard"|"ui", ...} 或 None 删除"""
        meta = self.load_meta(rec_id)
        sources = meta.get("var_sources", {})
        if source is None:
            sources.pop(var_name, None)
        else:
            sources[var_name] = source
        meta["var_sources"] = sources
        self.save_meta(rec_id, meta)

    # ---------- 阶段4.2：定时调度配置 ----------

    def schedule(self, rec_id: str) -> dict | None:
        meta = self.load_meta(rec_id)
        return meta.get("schedule")

    def set_schedule(self, rec_id: str, sched: dict | None):
        """sched: {kind: "daily"|"interval", hour?: int, minute?: int, minutes?: int}"""
        meta = self.load_meta(rec_id)
        meta["schedule"] = sched
        self.save_meta(rec_id, meta)

    # ---------- 阶段2.4：会话步骤标记 ----------

    def is_session_step(self, rec_id: str, step_index: int) -> bool:
        meta = self.load_meta(rec_id)
        return step_index in meta.get("session_steps", [])

    def set_session_step(self, rec_id: str, step_index: int, on: bool):
        meta = self.load_meta(rec_id)
        steps = set(meta.get("session_steps", []))
        if on:
            steps.add(step_index)
        else:
            steps.discard(step_index)
        meta["session_steps"] = sorted(steps)
        self.save_meta(rec_id, meta)
        task_path = self.task_path(rec_id)
        if task_path.exists():
            try:
                task = json.loads(task_path.read_text(encoding="utf-8"))
                self._refresh_plan_after_edit(rec_id, task, "session_step_changed")
            except (OSError, ValueError):
                pass
