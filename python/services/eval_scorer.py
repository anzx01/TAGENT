"""
确定性 Eval 评分器（移植自 skill-recorder evals/scoring.ts）
- 无 LLM 的规则评分：录制步骤(期望) vs 回放 REPORT(实际)
- 核心指标：有序子序列匹配(sequence_ratio)、成功率(action_accuracy)、噪音率(noise_ratio)
- 纯函数，无 I/O，可独立测试；接入 server._write_report
"""
from dataclasses import dataclass, field

__all__ = ["EvalConfig", "EvalScore", "score"]

# 参与子序列匹配的动作类型（wait/switch_window 等环境性步骤不参与评分）
_SCORE_ACTIONS = {"click", "right_click", "double_click", "input_text",
                  "key_press", "scroll", "drag", "swipe"}


@dataclass(frozen=True)
class EvalConfig:
    ok_threshold: float = 0.8      # 通过阈值（sequence 与 accuracy 均须 >=）
    noise_tolerance: float = 0.3   # 噪音容忍比率上限
    intent_min_len: int = 2        # 参与意图命中的最短 token


@dataclass
class EvalScore:
    total: int = 0                 # 期望（录制）计分步数
    matched: int = 0               # 有序子序列匹配步数
    ok_count: int = 0              # 实际成功步数
    noise: int = 0                 # 实际未匹配步数
    sequence_ratio: float = 0.0
    action_accuracy: float = 0.0
    noise_ratio: float = 0.0
    intent_hits: list[str] = field(default_factory=list)
    intent_total: int = 0
    verdict: str = "fail"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "total": self.total,
            "matched": self.matched,
            "ok_count": self.ok_count,
            "noise": self.noise,
            "sequence_ratio": round(self.sequence_ratio, 3),
            "action_accuracy": round(self.action_accuracy, 3),
            "noise_ratio": round(self.noise_ratio, 3),
            "intent": {"hits": self.intent_hits, "total": self.intent_total},
        }


def _actions(steps: list) -> list:
    """提取参与评分的动作序列"""
    return [s.get("action", "") for s in steps if s.get("action", "") in _SCORE_ACTIONS]


def _lcs_len(a: list[str], b: list[str]) -> int:
    """最长公共子序列长度（DP）"""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 0
    prev = [0] * (m + 1)
    for ai in a:
        row = [0] * (m + 1)
        for j, bj in enumerate(b, start=1):
            row[j] = prev[j - 1] + 1 if ai == bj else max(prev[j], row[j - 1])
        prev = row
    return prev[m]


def _intent_tokens(steps: list, config: EvalConfig) -> list[str]:
    """从期望 input_text 提取意图 token（中文按 2 字符滑动? 不：按条目切分）"""
    tokens: list[str] = []
    for s in steps:
        if s.get("action") != "input_text":
            continue
        text = str(s.get("text", ""))
        if len(text) >= config.intent_min_len:
            tokens.append(text)
    return tokens


def _text_set(steps: list) -> set[str]:
    out: set[str] = set()
    for s in steps:
        t = str(s.get("text", ""))
        if t:
            out.add(t)
    return out


def score(expected: list, actual: list, config: EvalConfig | None = None) -> EvalScore:
    """对录制步骤(expected)与回放结果(actual REPORT steps)评分"""
    cfg = config or EvalConfig()
    exp_a = _actions(expected)
    act_a = _actions(actual)
    matched = _lcs_len(exp_a, act_a)
    total = len(exp_a)
    ok_count = sum(1 for s in actual
                   if s.get("action") in _SCORE_ACTIONS and s.get("status") == "ok")
    noise = max(0, len(act_a) - matched)
    # 意图：期望中的 input_text 在回放步骤文本里出现
    want = _intent_tokens(expected, cfg)
    have = _text_set(actual)
    hits = [t for t in want if any(t in h for h in have)]
    score_ = EvalScore(
        total=total, matched=matched, ok_count=ok_count, noise=noise,
        sequence_ratio=(matched / total) if total else 0.0,
        action_accuracy=(ok_count / total) if total else 0.0,
        noise_ratio=(noise / len(act_a)) if act_a else 0.0,
        intent_hits=hits, intent_total=len(want),
    )
    score_.verdict = (
        "pass"
        if total > 0
        and score_.sequence_ratio >= cfg.ok_threshold
        and score_.action_accuracy >= cfg.ok_threshold
        and score_.noise_ratio <= cfg.noise_tolerance
        else "fail"
    )
    return score_


def forbidden_hits(actual: list, forbidden: list[str]) -> list[str]:
    """回放步骤(文本/窗口)命中禁用噪声词(录制器自身 UI、权限对话框等)即列出
    (移植 skill-recorder forbidden 一票否决;调用方非空即判 fail)。"""
    blob = " ".join(f"{s.get('text', '')} {s.get('window', '')}"
                    for s in actual if isinstance(s, dict)).casefold()
    return [f for f in forbidden if f and str(f).casefold() in blob]


if __name__ == "__main__":
    exp = [{"action": "click"}, {"action": "input_text", "text": "hello"},
           {"action": "click"}, {"action": "input_text", "text": "world"}]
    act = [{"action": "click", "status": "ok"}, {"action": "input_text", "text": "hello", "status": "ok"},
           {"action": "click", "status": "ok"}, {"action": "input_text", "text": "world", "status": "ok"}]
    r = score(exp, act)
    print(r.to_dict())