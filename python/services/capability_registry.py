"""能力注册表（移植 ui-regression-recorder-skill flows.json）
- 把录制任务的某段步骤登记为可复用「能力」，供后续任务组装/复用
- 存储：data/capabilities.json；纯函数数据层，可独立测试
"""
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

__all__ = ["CapabilityEntry", "CapabilityRegistry"]


@dataclass
class CapabilityEntry:
    source_task: str          # 源录制 id
    name: str
    step_range: list[int]     # task.json 步骤区间 [start, end)
    description: str = ""
    source_hash: str = ""
    postcondition: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)
    var_sources: dict = field(default_factory=dict)
    session_steps: list[int] = field(default_factory=list)
    # 依赖图(移植 flows.json):前置/依赖用于组合排序与失败回溯
    preconditions: list[dict] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    runs: int = 0
    last_used: str = ""
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


class CapabilityRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._entries: list[CapabilityEntry] = self._load()

    def _load(self) -> List[CapabilityEntry]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return [CapabilityEntry(**e) for e in raw if "source_task" in e]
        except Exception:
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            [e.__dict__ for e in self._entries], ensure_ascii=False, indent=2),
            encoding="utf-8")

    def _refresh(self) -> None:
        self._entries = self._load()

    def list(self) -> List[dict]:
        self._refresh()
        return [e.__dict__ for e in self._entries]

    def register(self, name: str, source_task: str,
                 step_range: List[int], description: str = "", source_hash: str = "",
                 postcondition: dict | None = None, steps: List[dict] | None = None,
                 var_sources: dict | None = None, session_steps: List[int] | None = None,
                 preconditions: List[dict] | None = None,
                 depends_on: List[str] | None = None) -> CapabilityEntry:
        self._refresh()
        name = name.strip()
        if not name or not source_task or len(step_range) != 2:
            raise ValueError("name/source_task/step_range[start,end) 必填")
        entry = CapabilityEntry(name=name, source_task=source_task,
                                step_range=step_range, description=description,
                                source_hash=source_hash, postcondition=postcondition or {},
                                steps=steps or [], var_sources=var_sources or {},
                                session_steps=session_steps or [],
                                preconditions=preconditions or [],
                                depends_on=depends_on or [])
        self._entries.append(entry)
        self._save()
        return entry

    def delete(self, cap_id: str) -> bool:
        self._refresh()
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.id != cap_id]
        if len(self._entries) != before:
            self._save()
            return True
        return False

    def mark_used(self, cap_id: str) -> bool:
        self._refresh()
        for e in self._entries:
            if e.id == cap_id:
                e.runs += 1
                e.last_used = time.strftime("%Y-%m-%d %H:%M:%S")
                self._save()
                return True
        return False

    def detach_source(self, source_task: str, task: dict | None = None,
                      meta: dict | None = None) -> None:
        """Keep capabilities usable after their source recording is removed."""
        self._refresh()
        for entry in self._entries:
            if entry.source_task == source_task:
                if not entry.steps and task:
                    start, end = entry.step_range
                    entry.steps = task.get("steps", [])[start:end]
                    entry.var_sources = (meta or {}).get("var_sources", {})
                    entry.session_steps = [index - start for index in
                                           (meta or {}).get("session_steps", [])
                                           if start <= index < end]
                entry.source_task = ""
                entry.step_range = [0, len(entry.steps)]
        self._save()


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        reg = CapabilityRegistry(Path(td) / "capabilities.json")
        e = reg.register("打开选股列表", "20260816_090104", [3, 9], "导航到列表页")
        assert reg.list()[0]["name"] == "打开选股列表"
        assert reg.mark_used(e.id) and reg.list()[0]["runs"] == 1
        assert reg.delete(e.id) and not reg.list()
    print("capability_registry 自检通过")
