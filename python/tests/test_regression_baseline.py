"""golden-vs-buggy 回归基线单测。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import regression_baseline as rb


def main():
    report = {"success": True, "source_hash": "h1", "plan_revision": 2,
              "steps": [{"action": "click", "status": "ok"},
                        {"action": "input_text", "status": "ok"},
                        {"action": "verify_capability", "status": "ok",
                         "verification": "passed", "capability": {"name": "提交"}}]}
    base = rb.baseline_from_run(report)
    assert base["actions"] == ["click", "input_text"] and base["verified"] == ["提交"]

    with tempfile.TemporaryDirectory() as td:
        assert rb.compare(td, report) == {"checked": False}  # 无基线不比
        rb.save(td, report)
        ok_run = {"success": True, "steps": [{"action": "click"}, {"action": "key_press"},
                                             {"action": "input_text"}]}
        assert rb.compare(td, ok_run)["pass"]  # 允许新增步骤,顺序保持
        lost = {"success": True, "steps": [{"action": "input_text"}]}
        result = rb.compare(td, lost)
        assert not result["pass"] and result["missing"] == ["click"]
        failed = {"success": False, "steps": [{"action": "click"}, {"action": "input_text"}]}
        assert not rb.compare(td, failed)["pass"]

    degraded = rb.degrade({"steps": [
        {"action": "click",
         "locator": [{"type": "uia", "name": "btn"}, {"type": "coord", "x": 5, "y": 6},
                     {"type": "screen_coord", "x": 1, "y": 2}]},
        {"action": "input_text",
         "locator": [{"type": "ocr", "text": "搜索", "client_pct_x": 50.0, "client_pct_y": 10.0}]},
    ]})
    loc = degraded["steps"][0]["locator"]
    assert loc == [{"type": "screen_coord", "x": 20001, "y": 20002}], loc  # 只留最低置信候选并偏移
    assert degraded["steps"][1]["locator"][0]["client_pct_x"] == 40.0
    assert degraded["expected_verdict"] == "fail"
    print("regression_baseline PASS")


if __name__ == "__main__":
    main()
