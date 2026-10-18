"""Capability completion checks distinguish observed and unobserved outcomes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.capability_verifier import evaluate_boundary, verify_postcondition


def main():
    assert verify_postcondition({"type": "window", "value": "订单已保存"},
                                window_title="订单已保存 - 商家后台")[0]
    assert not verify_postcondition({"type": "window", "value": "订单已保存"},
                                    window_title="编辑订单")[0]
    assert verify_postcondition({"type": "text", "value": "提交成功"}, text_found=True)[0]
    assert not verify_postcondition({"type": "text", "value": "提交成功"}, text_found=False)[0]
    assert not verify_postcondition({"type": "text", "value": ""}, text_found=True)[0]
    attempts = iter([(False, "not found"), (True, "found")])
    retried = evaluate_boundary({"type": "text", "value": "saved"},
                                lambda _: next(attempts), lambda _: "retry")
    assert retried == {"status": "ok", "verification": "passed", "detail": "found"}
    confirmed = evaluate_boundary({"type": "text", "value": "saved"},
                                  lambda _: (False, "not found"), lambda _: "skip")
    assert confirmed["status"] == "ok" and confirmed["verification"] == "human_confirmed"
    stopped = evaluate_boundary({"type": "text", "value": "saved"},
                                lambda _: (False, "not found"), lambda _: "stop")
    assert stopped["status"] == "failed"
    print("capability_verifier PASS")


if __name__ == "__main__":
    main()
