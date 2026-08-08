"""
任务库 - 在录制目录之上提供"任务"概念：
- 变量标注（阶段2.1）：步骤文本中的 {{var}} 占位符
- 定时调度配置（阶段4.2）
- 会话步骤标记（阶段2.4）
以 task.json + meta 注释持久化，不引入新存储。
"""
import json
from pathlib import Path


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

    # ---------- 阶段2.1：变量标注 ----------

    def variables(self, rec_id: str) -> list[str]:
        """扫描 task 步骤文本中的 {{var}} 占位符，返回变量名列表"""
        p = self.task_path(rec_id)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        names: list[str] = []
        import re
        seen = set()
        for s in data.get("steps", []):
            text = s.get("text", "") or ""
            for m in re.finditer(r"\{\{\s*(\w+)\s*\}\}", text):
                if m.group(1) not in seen:
                    seen.add(m.group(1))
                    names.append(m.group(1))
        return names

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
            step["_raw"] = raw
        step["text"] = f"{{{{ {var_name} }}}}" if var_name else raw
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

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