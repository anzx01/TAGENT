"""能力依赖图单测:pre/deps 字段、覆盖表、any_of 双期望验证。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.capability_compose import coverage
from services.capability_registry import CapabilityRegistry
from services.capability_verifier import verify_postcondition


def main():
    with tempfile.TemporaryDirectory() as td:
        reg = CapabilityRegistry(Path(td) / "capabilities.json")
        e = reg.register("登录", "r1", [0, 2], preconditions=[{"type": "window", "value": "门户"}],
                         depends_on=[])
        reg.register("提交表单", "r1", [2, 4], depends_on=[e.id])
        data = reg.list()
        assert data[0]["preconditions"] == [{"type": "window", "value": "门户"}]
        assert data[1]["depends_on"] == [e.id]
        reloaded = CapabilityRegistry(Path(td) / "capabilities.json").list()  # 持久化往返
        assert reloaded[1]["depends_on"] == [e.id]

    steps = [{"action": "click"}, {"action": "input_text"},
             {"action": "scroll"}, {"action": "click"}]
    entries = [{"id": "c1", "name": "填表", "steps": [{"action": "click"}, {"action": "input_text"}]}]
    cov = coverage(steps, entries)
    assert cov["reusable"] == [{"id": "c1", "capability": "填表", "range": [0, 2]}]
    assert cov["missing"] == [{"range": [2, 4]}]
    assert coverage(steps, [])["missing"] == [{"range": [0, 4]}]

    # any_of: 正向成功 + 反向拒绝,任一满足即过(双期望复用)
    ok, _ = verify_postcondition(
        {"any_of": [{"type": "window", "value": "成功"}, {"type": "window", "value": "已存在"}]},
        window_title="提示:已存在")
    assert ok
    ok2, msg = verify_postcondition(
        {"any_of": [{"type": "window", "value": "成功"}, {"type": "window", "value": "已存在"}]},
        window_title="错误")
    assert not ok2 and "成功" in msg and "已存在" in msg
    assert verify_postcondition({"any_of": []})[0] is False
    print("capability_deps PASS")


if __name__ == "__main__":
    main()
