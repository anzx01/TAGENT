"""eval_scorer 纯函数单测（直接运行：python tests/test_eval_scorer.py）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.eval_scorer import score, EvalConfig

E = {"action": "click", "status": "ok"}
I = {"action": "input_text", "text": "hello", "status": "ok"}

# 1. 完美回放
exp = [{"action": "click"}, {"action": "input_text", "text": "hello"}, {"action": "click"}]
act = [dict(E), dict(I), dict(E)]
r = score(exp, act)
assert r.verdict == "pass", r.to_dict()
assert r.sequence_ratio == 1.0 and r.action_accuracy == 1.0 and r.noise_ratio == 0.0
assert r.intent_hits == ["hello"], "意图 token 应命中"

# 2. 缺步（顺序子序列仍部分匹配）
act2 = [dict(E), dict(E)]
r2 = score(exp, act2)
assert r2.matched == 2 and r2.sequence_ratio == 2 / 3
assert r2.verdict == "fail", "缺步不应 pass"

# 3. 乱序（LCS 只承认有序，顺序变化应降分）
act3 = [dict(E), dict(E), dict(I)]
r3 = score(exp, act3)
assert r3.matched == 2 and r3.sequence_ratio == 2 / 3, "乱序应降分"

# 4. 噪音（多余步骤）
act4 = [dict(E), dict(I), dict(E), {"action": "key_press", "key": "ctrl s", "status": "ok"}]
r4 = score(exp, act4)
assert r4.noise == 1 and r4.noise_ratio == 0.25

# 5. 空期望/空实际
assert score([], []).total == 0
assert score(exp, []).verdict == "fail"

# 6. 环境性步骤（wait/switch_window）不参与评分
exp5 = [{"action": "wait"}, {"action": "click"}, {"action": "switch_window"}]
act5 = [dict(E)]
r5 = score(exp5, act5)
assert r5.total == 1 and r5.sequence_ratio == 1.0, "环境步骤应被忽略"

# 7. pause 步骤算失败
act6 = [dict(E), {"action": "input_text", "text": "hello", "status": "paused"}]
r6 = score(exp, act6)
assert r6.action_accuracy < 1.0 and r6.verdict == "fail"

# 8. to_dict 可 JSON 序列化
import json
json.dumps(r.to_dict())
assert r4.to_dict()["noise_ratio"] == 0.25

# forbidden 一票否决:录制器自身 UI/权限框噪声不得成为步骤
from services.eval_scorer import forbidden_hits
act = [{"action": "click", "text": "", "window": "同意访问 - 权限对话框"},
       {"action": "input_text", "text": "hello", "window": "W"}]
assert forbidden_hits(act, ["权限对话框", "智能操作助手"]) == ["权限对话框"]
assert forbidden_hits(act, []) == []
assert forbidden_hits(act, ["不存在的词"]) == []

print("eval_scorer 全部断言通过")