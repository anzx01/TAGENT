"""compiler 语义折叠单测（直接运行：python tests/test_compiler_semantic.py）"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compiler import Compiler


def compile_steps(steps):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "events.json").write_text(
            json.dumps({"steps": steps, "count": len(steps)}), encoding="utf-8")
        Compiler(d).compile()
        return json.loads((d / "task.json").read_text(encoding="utf-8"))


def assert_artifacts(steps):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "events.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")
        Compiler(d).compile()
        for name in ("plan.json", "bundle.json", "review.json"):
            assert (d / name).exists(), name


def click(win, x=100, y=100, t=0.0):
    return {"action": "click", "t": t, "screen_x": x, "screen_y": y,
            "client_x": x, "client_y": y, "window": win,
            "win_size": [800, 600], "pct_x": 10.0, "pct_y": 10.0,
            "hwnd": 123, "uia": {"name": "btn", "type": "ButtonControl", "class": "Button", "auto_id": ""}}


def text(win, s, t, hwnd=123):
    return {"action": "input_text", "t": t, "text": s, "window": win,
            "win_size": [800, 600], "hwnd": hwnd}


# 1. 同窗口连续输入合并
steps = [click("记事本"), text("记事本", "Hel", 0.5), text("记事本", "lo", 0.7)]
assert_artifacts(steps)
t = compile_steps(steps)
inputs = [s for s in t["steps"] if s["action"] == "input_text"]
assert len(inputs) == 1 and inputs[0]["text"] == "Hello", t["steps"]

# 2. 跨窗口不合并
steps = [click("记事本"), text("记事本", "Hel", 0.5),
         {"action": "switch_window", "t": 1.0, "window": "微信", "hwnd": 456},
         text("微信", "hi", 1.2, hwnd=456)]
t = compile_steps(steps)
inputs = [s for s in t["steps"] if s["action"] == "input_text"]
assert len(inputs) == 2, f"跨窗口应保留两步: {inputs}"

# 3. 间隔超 5s 不合并
steps = [click("记事本"), text("记事本", "Hel", 0.5), text("记事本", "lo", 6.0)]
t = compile_steps(steps)
inputs = [s for s in t["steps"] if s["action"] == "input_text"]
assert len(inputs) == 2

# 4. 分段锚点：窗口变化产生新段，segment_lead 标记段首
steps = [click("记事本"), text("记事本", "a", 0.5),
         {"action": "switch_window", "t": 1.0, "window": "微信", "hwnd": 456},
         click("微信", 200, 200, 1.2)]
t = compile_steps(steps)
segs = [s.get("segment") for s in t["steps"]]
leads = [s.get("index") for s in t["steps"] if s.get("segment_lead")]
assert len(set(segs)) == 2, f"应有两段: {segs}"
assert leads == [0, 3], f"段首应为首个与第4步(switch 本身不做段首): {leads}"
assert t["segment_count"] == 2

# 5. 冗余 switch_window 过滤不产生孤儿段
steps = [click("记事本"),
         {"action": "switch_window", "t": 1.0, "window": "记事本", "hwnd": 123},
         click("记事本", 300, 300, 1.5)]
t = compile_steps(steps)
segs = [s.get("segment") for s in t["steps"]]
assert len(set(segs)) == 1, f"冗余 switch 不应分段: {segs}"

print("compiler 语义折叠全部断言通过")
