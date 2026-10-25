"""意图分析单测:启发式脱敏、LLM 白名单合并、失败降级。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.intent_analyst import analyze, heuristic_analysis, merge_llm_polish

TASK = {"name": "报销", "steps": [
    {"action": "input_text", "text": "{{ order_id }}", "window": "报销 - Chrome"},
    {"action": "click", "text": "secret-label", "window": "报销 - Chrome"},
    {"action": "wait", "duration": 1},
]}


def main():
    a = heuristic_analysis(TASK)
    assert [s["id"] for s in a["steps"]] == ["s1", "s2", "s3"]
    assert a["steps"][0]["kind"] == "action" and a["steps"][2]["kind"] == "calculation"
    assert "{{ order_id }}" in a["steps"][0]["title"]
    assert "secret-label" not in str(a)  # 敏感文本只取长度
    assert a["generated_by"] == "heuristic"

    # LLM 只能改 title/intent/既有步骤标题;不能增删步骤或注入未知 id
    m = merge_llm_polish(a, {"title": "T", "intent": "I", "extra": "x",
                             "steps": [{"id": "s1", "title": "填订单号"},
                                       {"id": "sNEW", "title": "多余"}]})
    assert m["title"] == "T" and m["intent"] == "I" and "extra" not in m
    assert len(m["steps"]) == 3 and m["steps"][0]["title"] == "填订单号"
    assert m["steps"][1]["kind"] == "action"  # kind 不可被 LLM 改

    # LLM 异常 → 静默降级为启发式
    class Boom:
        def ask_json(self, prompt):
            raise RuntimeError("down")
    assert analyze(TASK, llm_client=Boom())["generated_by"] == "heuristic"
    print("intent_analyst PASS")


if __name__ == "__main__":
    main()
