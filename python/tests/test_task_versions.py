"""task_versions 单测（直接运行：python tests/test_task_versions.py）"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.task_versions import TaskVersions, diff_tasks


def task(steps, **extra):
    base = {"version": 2, "source": "t", "step_count": len(steps),
            "segment_count": 1, "steps": steps}
    base.update(extra)
    return base


def click_step(i, text=None, t=1.0):
    s = {"index": i, "action": "click", "t": t, "window": "记事本",
         "locator": [{"type": "uia", "name": "btn", "confidence": 0.95}],
         "effect": "mutating"}
    if text:
        s["text"] = text
    return s


# ---------- 1. 快照递增与幂等 ----------

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    tv = TaskVersions(d)

    v1 = task([click_step(0)])
    e1 = tv.snapshot(v1, "compiled")
    assert e1 and e1["n"] == 1 and e1["reason"] == "compiled"

    # 内容未变 -> 幂等跳过
    assert tv.snapshot(json.loads(json.dumps(v1)), "compiled") is None
    assert tv.manifest()["current"] == 1

    # 时间戳/索引变化不算语义变化（合并会重排 t）
    v1b = json.loads(json.dumps(v1))
    v1b["steps"][0]["t"] = 99.0
    v1b["steps"][0]["index"] = 7
    assert tv.snapshot(v1b, "compiled") is None, "易变字段变化不应产生新版本"

    # 步骤变化 -> v2
    v2 = task([click_step(0), click_step(1, text="hello")])
    e2 = tv.snapshot(v2, "merged")
    assert e2 and e2["n"] == 2 and e2["step_count"] == 2

    mf = tv.manifest()
    assert mf["current"] == 2 and [h["reason"] for h in mf["history"]] == ["compiled", "merged"]

    # 快照不可变：load 能读回
    assert tv.load(1)["step_count"] == 1
    assert tv.load(2)["step_count"] == 2
    assert tv.load(99) is None

# ---------- 2. 结构化 diff ----------

old = task([click_step(0), click_step(1), click_step(2)])
new = task([click_step(0), click_step(1, text="world"), click_step(2), click_step(3)],
           warning="weak_locators")

entries = diff_tasks(old, new)
types = {(e["field"], e["type"]) for e in entries}
assert ("steps[1]", "changed") in types, types
assert ("steps[3]", "added") in types, types
assert ("warning", "changed") in types, types
assert ("step_count", "changed") in types, types
changed = next(e for e in entries if e["field"] == "steps[1]" and e["type"] == "changed")
assert changed["after"].get("text") == "world"
assert changed["before"].get("text") is None
# 摘要不携带全量定位证据
assert "locator" in changed["after"] and isinstance(changed["after"]["locator"], str)

# 删除步骤
entries2 = diff_tasks(new, old)
assert ("steps[3]", "removed") in {(e["field"], e["type"]) for e in entries2}

# 完全一致（除易变字段）-> 空 diff
a = task([click_step(0, t=1.0)])
b = task([click_step(0, t=5.0)])
assert diff_tasks(a, b) == [], "时间戳不同不应产生 diff"

# ---------- 3. diff_vs_current ----------

with tempfile.TemporaryDirectory() as td:
    tv = TaskVersions(Path(td))
    tv.snapshot(task([click_step(0)]), "compiled")
    tv.snapshot(task([click_step(0), click_step(1)]), "merged")
    d1 = tv.diff_vs_current(1)
    assert d1 and any(e["type"] == "added" for e in d1)
    assert tv.diff_vs_current(2) is None, "与当前版本比较应返回 None"
    assert tv.diff_vs_current(99) is None

# ---------- 4. 编译器集成：compile() 自动产生快照 ----------

from compiler import Compiler

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d / "events.json").write_text(json.dumps({"steps": [
        {"action": "click", "t": 1, "window": "记事本", "hwnd": 1,
         "screen_x": 10, "screen_y": 10, "uia": {"name": "btn", "type": "Button"}},
    ]}), encoding="utf-8")
    Compiler(str(d)).compile()
    mf = TaskVersions(d).manifest()
    assert mf["current"] == 1 and mf["history"][0]["reason"] == "compiled"
    # 重编译未变 -> 不新增版本
    Compiler(str(d)).compile()
    assert TaskVersions(d).manifest()["current"] == 1

print("task_versions 全部断言通过")
