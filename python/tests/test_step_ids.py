"""稳定 step_id / 场景段 / checkpoint 按 id 对齐单测。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compiler import Compiler, _assign_stable_ids, _scenes
from services.checkpoints import resolve_resume_index


def _task(steps):
    return {"steps": steps}


def main():
    old = [{"action": "input_text", "window": "A - Notepad", "text": "hello", "step_id": "s1"},
           {"action": "click", "window": "A - Notepad", "step_id": "s2"},
           {"action": "key_press", "window": "A - Notepad", "key": "enter", "step_id": "s3"}]
    # 删掉中间步骤 + 末尾加新步骤:保留的步骤 id 不变
    new = [{"action": "input_text", "window": "A - Notepad", "text": "hello"},
           {"action": "key_press", "window": "A - Notepad", "key": "enter"},
           {"action": "click", "window": "B - Chrome"}]
    _assign_stable_ids(new, old)
    assert [s["step_id"] for s in new] == ["s1", "s3", "s4"], new
    # 定位变化不改 id(指纹不含定位)
    new2 = [dict(new[0], locator=[{"type": "uia"}])]
    _assign_stable_ids(new2, new)
    assert new2[0]["step_id"] == "s1"

    scenes = _scenes(new)
    assert [(s["window"], s["from"], s["to"]) for s in scenes] == \
        [("A - Notepad", 0, 2), ("B - Chrome", 2, 3)]

    # checkpoint:步骤删除后按 step_id 对齐到新 index
    with tempfile.TemporaryDirectory() as td:
        task = _task(new)
        ckpt = Path(td) / "ckpt.json"
        import json
        ckpt.write_text(json.dumps({"next_step": 2, "next_step_id": "s3",
                                    "source_hash": "same"}), encoding="utf-8")
        from services.workflow_plan import plan_source_hash
        task["source_hash"] = "same"  # 对齐逻辑只看 id,hash 由 read_checkpoint 校验
        import services.checkpoints as cp
        orig = cp.plan_source_hash
        cp.plan_source_hash = lambda t: "same"
        try:
            assert resolve_resume_index(ckpt, task, requested=2) == 1  # s3 现在是 index 1
        finally:
            cp.plan_source_hash = orig

    # Compiler 端到端:编译产物带 step_id 与 scenes
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "events.json").write_text(json.dumps({"steps": [
            {"action": "input_text", "text": "a", "window": "W", "t": 0.1, "screen_x": 1, "screen_y": 1},
            {"action": "key_press", "key": "enter", "window": "W", "t": 0.2},
        ]}), encoding="utf-8")
        Compiler(d).compile()
        task = json.loads((d / "task.json").read_text(encoding="utf-8"))
        assert all(s.get("step_id") for s in task["steps"])
        assert task["scenes"] and task["scenes"][0]["window"] == "W"
        old_ids = [s["step_id"] for s in task["steps"]]
        (d / "events.json").write_text(json.dumps({"steps": [
            {"action": "input_text", "text": "a", "window": "W", "t": 0.1, "screen_x": 1, "screen_y": 1},
            {"action": "key_press", "key": "enter", "window": "W", "t": 0.2},
            {"action": "click", "window": "W", "t": 0.5, "screen_x": 5, "screen_y": 5},
        ]}), encoding="utf-8")
        Compiler(d).compile()
        task2 = json.loads((d / "task.json").read_text(encoding="utf-8"))
        assert [s["step_id"] for s in task2["steps"][:2]] == old_ids  # 重编译保留旧 id
    print("step_ids PASS")


if __name__ == "__main__":
    main()
