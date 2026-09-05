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
                value = json.loads(self.path.read_text(encoding="utf-8"))
                return value if isinstance(value, list) else []
            except Exception:
                return []
        return []

    def save(self, corrections: list[dict]):
        """Persist atomically so a crash cannot leave a half-written patch list."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

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
            "approved": False,
            "approved_revision": None,
            "active_revision": None,
            "versions": [],
            "history": [],
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
        # Formal replay only consumes an explicitly approved active revision.
        # Callers that want to run an isolated trial must opt into the private
        # ``_best_candidate(..., approved_only=False)`` path; the normal API is
        # deliberately fail-closed.
        corr = self._best_candidate(sig, approved_only=True)
        if corr is None:
            return None
        return corr["correct"]

    def confidence(self) -> float:
        corrs = self.load()
        if not corrs:
            return 0.0
        approved = [c for c in corrs if c.get("approved") and c.get("active_revision") is not None]
        if not approved:
            return 0.0
        return min(1.0, sum(c.get("frozen", 0) for c in approved) / (len(approved) * 3))

    # ---------- 3.3 信任机制 ----------

    def record_trial(self, corr_id: str, success: bool):
        """Record an isolated trial without changing the approved revision."""
        corrections = self.load()
        for c in corrections:
            if c["id"] == corr_id:
                c["frozen"] = c["frozen"] + 1 if success else 0
                c["trials"] = int(c.get("trials", 0)) + 1
                c["successes"] = int(c.get("successes", 0)) + (1 if success else 0)
                c["failures"] = int(c.get("failures", 0)) + (0 if success else 1)
                c["success_rate"] = round(
                    c["successes"] / c["trials"], 3) if c["trials"] else 0.0
                c.setdefault("trial_history", []).append({
                    "success": bool(success), "ts": time.time(),
                })
        self.save(corrections)

    def approve(self, corr_id: str) -> dict | None:
        corrections = self.load()
        for c in corrections:
            if c.get("id") == corr_id:
                next_revision = int(c.get("approved_revision") or 0) + 1
                c["approved"] = True
                c["trusted"] = True
                c["approved_revision"] = next_revision
                c["active_revision"] = next_revision
                c.setdefault("versions", []).append({
                    "revision": next_revision,
                    "correct": [self._compact(s) for s in c.get("correct", [])],
                    "approved_at": time.time(),
                })
                c.setdefault("history", []).append({
                    "revision": next_revision, "action": "approve", "ts": time.time()
                })
                self.save(corrections)
                return c
        return None

    def rollback(self, corr_id: str) -> dict | None:
        corrections = self.load()
        for c in corrections:
            if c.get("id") == corr_id:
                c["approved"] = False
                c["trusted"] = False
                c["active_revision"] = None
                c.setdefault("history", []).append({
                    "revision": c.get("approved_revision"), "action": "rollback", "ts": time.time()
                })
                self.save(corrections)
                return c
        return None

    # ---------- 内部 ----------

    def _best_candidate(self, sig: str, *, approved_only: bool = False):
        best = None
        best_score = -1
        for corr in self.load():
            if approved_only and (
                not bool(corr.get("approved", False))
                or ("active_revision" in corr and corr.get("active_revision") is None)
            ):
                continue
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
