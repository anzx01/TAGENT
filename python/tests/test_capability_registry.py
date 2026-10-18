"""capability_registry 单测（直接运行：python tests/test_capability_registry.py）"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.capability_registry import CapabilityRegistry


def test_crud():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "capabilities.json"
        reg = CapabilityRegistry(path)
        entry = reg.register("打开选股列表", "20260816_090104", [3, 9], "导航到列表页")
        assert entry.id and entry.created_at
        # 持久化：重新加载仍有数据
        reg2 = CapabilityRegistry(path)
        assert reg2.list()[0]["source_task"] == "20260816_090104"
        assert reg2.list()[0]["step_range"] == [3, 9]
        reg.detach_source("20260816_090104", {"steps": [{"action": "click"}] * 9})
        assert reg2.list()[0]["source_task"] == ""
        assert len(reg2.list()[0]["steps"]) == 6
        # 校验必填
        for bad in (("", "t", [0, 1]), ("n", "", [0, 1]), ("n", "t", [0])):
            try:
                reg.register(*bad)
                raise AssertionError("should raise")
            except ValueError:
                pass
        # 统计与删除
        assert reg.mark_used(entry.id)
        assert reg.list()[0]["runs"] == 1 and reg.list()[0]["last_used"]
        assert not reg.mark_used("nope")
        assert reg.delete(entry.id)
        assert not reg.delete(entry.id)


def test_corrupt_file():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "capabilities.json"
        path.write_text("{broken", encoding="utf-8")
        reg = CapabilityRegistry(path)
        assert reg.list() == []


test_crud()
test_corrupt_file()
print("capability_registry 全部断言通过")
