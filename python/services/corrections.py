"""
纠错库（阶段3：人在回路自愈）
- 3.2 纠错捕获：人工接管时录制正确动作，保存为 correction
- 3.3 自愈：回放某步失败/不匹配时，查纠错库按"触发状态"匹配替代步骤
- 信任机制：先试用（试用次数计数器），多次稳定后算固化（借用 skillwright 思想，简单版）
存储：tasks/<rec_id>/corrections.json
"""
import json
import time
from pathlib import Path

from logger import get_logger

log = get_logger("corrections")


class CorrectionStore:
    def __init__(self, recording_dir: Path):
        self.path = Path(recording_dir) / "corrections.json"

    def load(self) -> list[dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def save(self, corrections: list[dict]):
        self.path.write_text(
            json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 3.2 纠错捕获 ----------

    def add_correction(self, trigger_actions: list[dict], correct_actions: list[dict]) -> dict:
        """记录一次纠错：trigger 是出错的步骤序列（依据），correct 是人工正确操作。"""
        corr = {
            "id": f"corr_{len(self.load())+1}",
            "trigger": [self._compact(s) for s in trigger_actions],
            "correct": [self._compact(s) for s in correct_actions],
            "trials": 0,
            "frozen": 0,      # 连续试用成功次数
            "trusted": False,
        }
        corrections = self.load()
        corrections.append(corr)
        self.save(corrections)
        log.info("纠错已入库: %s (%d 步)", corr["id"], len(correct_actions))
        return corr

    def match(self, actions: list[dict]) -> list[dict] | None:
        """按触发状态下拉候选：返回正确动作列表；查不到返回 None"""
        if not actions:
            return None
        sig = self._signal(actions)
        corr = self._best_candidate(sig)
        if corr is None:
            return None
        return corr["correct"]

    def confidence(self) -> float:
        corrs = self.load()
        if not corrs:
            return 0.0
        return min(1.0, sum(c.get("frozen", 0) for c in corrs) / (len(corrs) * 3))

    # ---------- 3.3 信任机制 ----------

    def record_trial(self, corr_id: str, success: bool):
        """试用成功后固化计数+1；失败清零。"""
        corrections = self.load()
        for c in corrections:
            if c["id"] == corr_id:
                c["frozen"] = c["frozen"] + 1 if success else 0
        self.save(corrections)

    # ---------- 内部 ----------

    def _best_candidate(self, sig: str):
        best = None
        best_score = -1
        for corr in self.load():
            score = self._score(corr, sig)
            if score > best_score:
                best_score = score
                best = corr
        if best is None or best_score <= 0:
            return None
        return best

    def _score(self, corr: dict, sig: str) -> float:
        trigger = corr.get("trigger", [])
        if not trigger:
            return 0
        # 触发状态末尾步骤与当前信号重合度
        t_sig = "|".join(self._step_signal(s) for s in trigger[-2:])
        overlap = 0
        for part in sig.split("|"):
            if part and part in t_sig:
                overlap += 1
        if overlap == 0:
            return 0.0
        return overlap / max(len(sig.split("|")), 1)

    def _signal(self, actions: list[dict]) -> str:
        return "|".join(self._step_signal(s) for s in actions[-2:])

    @staticmethod
    def _step_signal(step: dict) -> str:
        action = step.get("action", "?")
        text = str(step.get("text", step.get("key", "")))[:40]
        uia = step.get("uia") or {}
        name = str(uia.get("name", ""))[:30]
        return f"{action}:{text}:{name}"

    @staticmethod
    def _compact(step: dict) -> dict:
        keep = ("action", "text", "key", "screen_x", "screen_y",
                "pct_x", "pct_y", "client_x", "client_y", "window")
        return {k: v for k, v in step.items() if k in keep and v is not None}