"""
任务库 API（阶段2.1/2.4/3/4.2/4.3）
- 变量标注 {{var}} + 运行时变量值
- 会话步骤标记
- 定时调度配置
- 纠错库增删查
- 密码框安全暂停
"""
import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from logger import get_logger
from services.capability_registry import CapabilityRegistry
from services.corrections import CorrectionStore
from services.scheduler import Scheduler
from services.task_store import TaskStore
from services.workflow_plan import build_plan, plan_source_hash
from services.workflow_artifacts import export_artifact
from services.discovery import rank_candidates
from services.security import get_security

log = get_logger("routes")


class ScheduleBody(BaseModel):
    kind: str = "interval"
    hour: int = 9
    minute: int = 0
    minutes: int = 60


class CorrectBody(BaseModel):
    trigger: list[dict] = []
    correct: list[dict] = []


class CapRegisterBody(BaseModel):
    name: str
    source_task: str
    step_range: list[int] = [0, 0]
    description: str = ""


class ExportBody(BaseModel):
    kind: str = "skill"


class ReviewBody(BaseModel):
    approved: bool
    feedback: list[str] = Field(default_factory=list)
    # Answers are keyed by the plan step index.  They live in review.json,
    # keeping the immutable plan/source untouched while making open questions
    # auditable and resumable.
    answers: dict[str, str] = Field(default_factory=dict)


def build_router(data_dir: Path, scheduler: Scheduler) -> APIRouter:
    """路由工厂：注入录制目录与调度器，避免全局状态循环依赖。"""
    router = APIRouter(prefix="/tasks", tags=["tasks"])
    store = TaskStore(data_dir)
    cap_registry = CapabilityRegistry(data_dir / "capabilities.json")

    def _resolve(rec_id: str) -> Path | None:
        import re
        if not re.fullmatch(r"\d{8}_\d{6}", rec_id):
            return None
        d = data_dir / rec_id
        return d if d.is_dir() else None

    @router.get("")
    def list_tasks():
        """任务卡片数据（阶段4.1）：名称/步数/调度/变量数/纠错数"""
        items = []
        for d in sorted(data_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            rec_id = d.name
            meta = store.load_meta(rec_id)
            count = 0
            ef = d / "events.json"
            if ef.exists():
                try:
                    count = json.loads(ef.read_text(encoding="utf-8")).get("count", 0)
                except Exception:
                    pass
            # The task card represents executable steps, not raw recorder
            # events. Compilation may merge several text events into one task
            # step, so prefer task.json when it is available.
            tf = d / "task.json"
            if tf.exists():
                try:
                    count = len(json.loads(tf.read_text(encoding="utf-8")).get("steps", []))
                except Exception:
                    pass
            corr_n = 0
            cf = d / "corrections.json"
            if cf.exists():
                try:
                    corr_n = len(json.loads(cf.read_text(encoding="utf-8")))
                except Exception:
                    pass
            run_count = 0
            run_dir = data_dir.parent / "runs"
            if run_dir.exists():
                run_count = sum(1 for _ in run_dir.glob(f"*_{rec_id}.json"))
            items.append({
                "id": rec_id,
                "name": meta.get("name", rec_id),
                "steps": count,
                "schedule": meta.get("schedule"),
                "vars": len(store.variables(rec_id)),
                "corrections": corr_n,
                "replayable": count > 0,
                "runs": run_count,
            })
        return {"ok": True, "tasks": items}

    @router.get("/{rec_id}/plan")
    def get_plan(rec_id: str):
        """Caddi-style Automation Plan: reviewable, non-executing metadata."""
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        task_file = d / "task.json"
        if not task_file.exists():
            return {"ok": False, "msg": "任务尚未编译"}
        try:
            task = json.loads(task_file.read_text(encoding="utf-8"))
            meta = store.load_meta(rec_id)
            plan_file = d / "plan.json"
            if plan_file.exists():
                plan = json.loads(plan_file.read_text(encoding="utf-8"))
            else:
                plan = task.get("plan") or build_plan(
                    task, session_steps=meta.get("session_steps", []),
                    revision=int(meta.get("plan_revision", 1)),
                )
            current_hash = plan_source_hash(task)
            if plan.get("source_hash") != current_hash:
                return {"ok": False, "msg": "方案已过期，请重新编译任务", "source_hash": current_hash}
            return {"ok": True, "plan": plan}
        except Exception as exc:
            log.exception("读取任务计划失败: %s", rec_id)
            return {"ok": False, "msg": str(exc)}

    @router.get("/discovery")
    def discover_workflows():
        """Scout: rank repetitive local workflows without collecting telemetry."""
        tasks = list_tasks().get("tasks", [])
        run_dir = data_dir.parent / "runs"
        runs_by_task: dict[str, list[dict]] = {}
        if run_dir.exists():
            for report_file in run_dir.glob("*.json"):
                try:
                    report = json.loads(report_file.read_text(encoding="utf-8"))
                    rec_id = str(report.get("rec_id", ""))
                    if not rec_id:
                        continue
                    steps = report.get("steps", [])
                    runs_by_task.setdefault(rec_id, []).append({
                        "success": bool(steps) and all(s.get("status") == "ok" for s in steps),
                        "pause_count": len(report.get("pause_reasons", [])),
                    })
                except (OSError, ValueError):
                    continue
        return {"ok": True, "candidates": rank_candidates(tasks, runs_by_task)}

    @router.get("/{rec_id}/runs")
    def list_runs(rec_id: str):
        """Run History: return summaries only; raw screenshots stay local."""
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        run_dir = data_dir.parent / "runs"
        out = []
        if run_dir.exists():
            # RunContext reports use an epoch-millisecond prefix (for
            # example ``178857...``), while legacy reports use a wall-clock
            # prefix (``20260905_...``).  Lexicographic filename ordering
            # therefore places a newer RunContext report *before or after*
            # the wrong legacy report.  Use filesystem write time as the
            # stable fallback ordering; the report is atomically written at
            # completion, so this reflects actual Run History order.
            report_files = list(run_dir.glob(f"*_{rec_id}.json"))
            report_files.sort(
                key=lambda p: (
                    p.stat().st_mtime_ns if p.exists() else 0,
                    p.name,
                ),
                reverse=True,
            )
            for report_file in report_files:
                try:
                    report = json.loads(report_file.read_text(encoding="utf-8"))
                    steps = report.get("steps", [])
                    out.append({
                        "run_id": report_file.stem,
                        "ts": report.get("ts"),
                        "started_at": (report.get("run_context") or {}).get("started_at"),
                        "finished_at": report.get("finished_at"),
                        "trigger": report.get("trigger", "manual"),
                        "plan_revision": report.get("plan_revision"),
                        "source_hash": report.get("source_hash"),
                        "summary": report.get("summary", ""),
                        "success": bool(steps) and all(s.get("status") == "ok" for s in steps),
                        "step_count": len(steps),
                        "pause_count": len(report.get("pause_reasons", [])),
                        "eval": report.get("eval"),
                    })
                except (OSError, ValueError):
                    continue
        return {"ok": True, "runs": out}

    @router.post("/{rec_id}/export")
    def export_task_artifact(rec_id: str, body: ExportBody):
        """Create an opt-in portable Skill or Automation artifact."""
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        if body.kind not in {"skill", "automation"}:
            return {"ok": False, "msg": "kind 必须是 skill 或 automation"}
        try:
            path = export_artifact(d, body.kind)
            return {"ok": True, "kind": body.kind, "path": str(path)}
        except Exception as exc:
            log.exception("导出任务产物失败: %s", rec_id)
            return {"ok": False, "msg": str(exc)}

    @router.get("/{rec_id}/review")
    def get_review(rec_id: str):
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        path = d / "review.json"
        review = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"approved": False, "feedback": []}
        return {"ok": True, "review": review}

    @router.put("/{rec_id}/review")
    def update_review(rec_id: str, body: ReviewBody):
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        plan_path = d / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
        if not plan:
            return {"ok": False, "msg": "任务尚未生成可审阅方案"}
        try:
            task_for_hash = json.loads((d / "task.json").read_text(encoding="utf-8"))
            if plan.get("source_hash") != plan_source_hash(task_for_hash):
                return {"ok": False, "msg": "方案已过期，请重新编译任务"}
        except (OSError, ValueError):
            return {"ok": False, "msg": "任务文件不可读"}
        path = d / "review.json"
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        answers = dict(current.get("answers") or {})
        answers.update({str(k): str(v) for k, v in body.answers.items() if str(v).strip()})
        unresolved = [
            q for q in plan.get("open_questions", [])
            if not str(answers.get(str(q.get("step")), "")).strip()
        ]
        if body.approved and unresolved:
            return {"ok": False, "msg": "仍有开放问题，不能批准", "open_questions": unresolved}
        now = __import__("time").time()
        prior = dict(current)
        current.update({
            "approved": bool(body.approved),
            "feedback": list(body.feedback),
            "answers": answers,
            "revision": int(plan.get("revision", 1)),
            "plan_revision": int(plan.get("revision", 1)),
            "source_hash": plan.get("source_hash", ""),
            "updated_at": now,
        })
        current.setdefault("history", []).append({
            "action": "approve" if body.approved else "revoke",
            "revision": current["revision"],
            "source_hash": current["source_hash"],
            "ts": now,
            "feedback": list(body.feedback),
        })
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return {"ok": True, "review": current}

    # ---------- task.json 版本历史（移植 openskills 版本快照） ----------

    @router.get("/{rec_id}/versions")
    def list_versions(rec_id: str):
        from services.task_versions import TaskVersions
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        mf = TaskVersions(d).manifest()
        return {"ok": True, "current": mf.get("current", 0), "history": mf.get("history", [])}

    @router.get("/{rec_id}/versions/{n}/diff")
    def diff_version(rec_id: str, n: int):
        from services.task_versions import TaskVersions
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        entries = TaskVersions(d).diff_vs_current(n)
        if entries is None:
            return {"ok": False, "msg": "版本不存在或已是当前版本"}
        return {"ok": True, "version": n, "diff": entries}

    # ---------- 阶段2.1 变量标注 ----------

    @router.get("/{rec_id}/vars")
    def get_variables(rec_id: str):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        return {"ok": True, "vars": store.variables(rec_id)}

    @router.put("/{rec_id}/vars/{step_index}")
    def set_variable(rec_id: str, step_index: int, var: str = ""):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        if store.set_variable(rec_id, step_index, var):
            return {"ok": True, "vars": store.variables(rec_id)}
        return {"ok": False, "msg": "步骤无效或非输入步骤"}

    # ---------- 阶段2.1 变量来源绑定 ----------

    @router.get("/{rec_id}/var_sources")
    def get_var_sources(rec_id: str):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        return {"ok": True, "sources": store.get_var_sources(rec_id)}

    @router.put("/{rec_id}/var_sources/{var_name}")
    def set_var_source(rec_id: str, var_name: str, body: dict = None):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        store.set_var_source(rec_id, var_name, body)
        return {"ok": True, "sources": store.get_var_sources(rec_id)}

    # ---------- 阶段2.4 会话步骤 ----------

    @router.put("/{rec_id}/session/{step_index}")
    def set_session(rec_id: str, step_index: int, on: bool = True):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        store.set_session_step(rec_id, step_index, on)
        return {"ok": True}

    # ---------- 阶段4.2 定时调度 ----------

    @router.put("/{rec_id}/schedule")
    def set_schedule(rec_id: str, body: ScheduleBody):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        scheduler.set_job(rec_id, body.model_dump())
        return {"ok": True}

    @router.delete("/{rec_id}/schedule")
    def delete_schedule(rec_id: str):
        scheduler.set_job(rec_id, None)
        return {"ok": True}

    # ---------- 阶段3 纠错库 ----------

    @router.get("/{rec_id}/corrections")
    def list_corrections(rec_id: str):
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        store_c = CorrectionStore(d)
        return {"ok": True, "corrections": store_c.load()}

    @router.delete("/{rec_id}/corrections/{corr_id}")
    def delete_correction(rec_id: str, corr_id: str):
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        store_c = CorrectionStore(d)
        corrs = [c for c in store_c.load() if c.get("id") != corr_id]
        store_c.save(corrs)
        return {"ok": True}

    @router.post("/{rec_id}/correct")
    def add_correction(rec_id: str, body: CorrectBody):
        """人工接管后提交纠错：trigger=出错步骤，correct=人工正确步骤"""
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        if not body.correct:
            return {"ok": False, "msg": "缺少正确步骤"}
        store_c = CorrectionStore(d)
        corr = store_c.add_correction(body.trigger, body.correct)
        return {"ok": True, "correction": corr}

    @router.post("/{rec_id}/corrections/{corr_id}/approve")
    def approve_correction(rec_id: str, corr_id: str):
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        corr = CorrectionStore(d).approve(corr_id)
        return {"ok": bool(corr), "correction": corr}

    @router.post("/{rec_id}/corrections/{corr_id}/rollback")
    def rollback_correction(rec_id: str, corr_id: str):
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        corr = CorrectionStore(d).rollback(corr_id)
        return {"ok": bool(corr), "correction": corr}

    # ---------- 阶段8 能力注册表（flows） ----------

    @router.get("/capabilities")
    def list_capabilities():
        return {"ok": True, "capabilities": cap_registry.list()}

    @router.post("/capabilities")
    def register_capability(body: CapRegisterBody):
        d = _resolve(body.source_task)
        if d is None:
            return {"ok": False, "msg": "找不到源任务"}
        tf = d / "task.json"
        if tf.exists():
            try:
                total = len(json.loads(tf.read_text(encoding="utf-8")))
            except Exception:
                total = 0
        else:
            total = 0
        start, end = body.step_range
        if not (0 <= start < end <= max(total, 1)):
            return {"ok": False, "msg": f"步骤区间越界（共 {total} 步）"}
        try:
            entry = cap_registry.register(body.name, body.source_task, [start, end], body.description)
        except ValueError as ex:
            return {"ok": False, "msg": str(ex)}
        return {"ok": True, "capability": entry.__dict__}

    @router.delete("/capabilities/{cap_id}")
    def delete_capability(cap_id: str):
        return {"ok": cap_registry.delete(cap_id)}

    @router.post("/capabilities/{cap_id}/use")
    def use_capability(cap_id: str):
        return {"ok": cap_registry.mark_used(cap_id)}

    # ---------- 阶段4.3 安全查询 ----------

    @router.get("/security/summary")
    def security_summary():
        sec = get_security()
        import ctypes, win32gui
        fg = win32gui.GetForegroundWindow()
        pid = ctypes.windll.user32.GetWindowThreadProcessId(fg, None) if fg else None
        exe = ""
        if pid:
            try:
                import psutil
                exe = psutil.Process(pid).name()
            except Exception:
                exe = ""
        return {
            "ok": True,
            "blacklisted": sec.is_blacklisted(exe),
            "password_edit": sec.is_password_edit(fg) if fg else False,
            "exe": exe,
        }

    return router
