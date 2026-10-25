"""
任务库 API（阶段2.1/2.4/3/4.2/4.3）
- 变量标注 {{var}} + 运行时变量值
- 会话步骤标记
- 定时调度配置
- 纠错库增删查
- 密码框安全暂停
"""
import json
import tempfile
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Literal

from logger import get_logger
from services.capability_registry import CapabilityRegistry
from services.corrections import CorrectionStore
from services.scheduler import Scheduler
from services.task_store import TaskStore
from services.workflow_plan import build_plan, plan_source_hash
from services.workflow_artifacts import export_artifact
from services.discovery import rank_candidates
from services.capability_compose import compose_task
from services.security import get_security
from services.checkpoints import read_checkpoint

log = get_logger("routes")


def _run_succeeded(report: dict) -> bool:
    if "success" in report:
        return bool(report["success"])
    steps = report.get("steps", [])
    return bool(steps) and all(step.get("status") == "ok" for step in steps)


class ScheduleBody(BaseModel):
    kind: Literal["interval", "daily"] = "interval"
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    minutes: int = Field(default=60, ge=1)


class CorrectBody(BaseModel):
    trigger: list[dict] = []
    correct: list[dict] = []


class CapRegisterBody(BaseModel):
    name: str
    source_task: str
    step_range: list[int] = [0, 0]
    description: str = ""
    postcondition: dict = Field(default_factory=dict)


class ComposeBody(BaseModel):
    name: str
    capability_ids: list[str]


class ExportBody(BaseModel):
    kind: str = "skill"


class ReviewBody(BaseModel):
    approved: bool
    feedback: list[str] = Field(default_factory=list)
    # Answers are keyed by the plan step index.  They live in review.json,
    # keeping the immutable plan/source untouched while making open questions
    # auditable and resumable.
    answers: dict[str, str] = Field(default_factory=dict)


def build_router(data_dir: Path, scheduler: Scheduler, autostart_sync=None) -> APIRouter:
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

    def _disable_schedule(rec_id: str) -> None:
        previous = scheduler.list_jobs().get(rec_id)
        configured = store.schedule(rec_id)
        if previous is None and configured is None:
            return
        try:
            if previous is not None:
                scheduler.set_job(rec_id, None)
            if autostart_sync and previous is not None:
                autostart_sync(bool(scheduler.list_jobs()))
            if configured is not None:
                store.set_schedule(rec_id, None)
        except Exception:
            if previous is not None:
                scheduler.set_job(rec_id, previous)
            try:
                if autostart_sync:
                    autostart_sync(bool(scheduler.list_jobs()))
            except Exception:
                log.exception("回滚 Windows 登录后台任务失败")
            raise

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
            task_data = None
            if tf.exists():
                try:
                    task_data = json.loads(tf.read_text(encoding="utf-8"))
                    count = len(task_data.get("steps", []))
                except Exception:
                    pass
            checkpoint = None
            checkpoint_file = data_dir.parent / "data" / f"ckpt_{rec_id}.json"
            if task_data is not None and checkpoint_file.exists():
                saved = read_checkpoint(checkpoint_file, task_data)
                try:
                    next_step = int(saved.get("next_step", 0)) if saved and not saved["stale"] else 0
                    if 0 < next_step < count:
                        checkpoint = {"next_step": next_step, "total": count}
                except (TypeError, ValueError):
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
                "checkpoint": checkpoint,
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
                        "success": _run_succeeded(report),
                        "pause_count": len(report.get("pause_reasons", [])),
                    })
                except (OSError, ValueError):
                    continue
        return {"ok": True, "candidates": rank_candidates(tasks, runs_by_task)}

    @router.get("/{rec_id}/runs")
    def list_runs(rec_id: str):
        """Return run summaries; individual reports can be opened on demand."""
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
                        "success": _run_succeeded(report),
                        "step_count": len(steps),
                        "pause_count": len(report.get("pause_reasons", [])),
                        "failed_capability": report.get("failed_capability"),
                        "baseline_pass": report.get("baseline", {}).get("pass")
                        if report.get("baseline", {}).get("checked") else None,
                        "eval": report.get("eval"),
                    })
                except (OSError, ValueError):
                    continue
        return {"ok": True, "runs": out}

    @router.get("/{rec_id}/runs/{run_id}")
    def get_run(rec_id: str, run_id: str):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            return {"ok": False, "msg": "运行记录编号无效"}
        report_file = data_dir.parent / "runs" / f"{run_id}.json"
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "msg": "找不到运行记录"}
        if report.get("rec_id") != rec_id:
            return {"ok": False, "msg": "找不到运行记录"}
        steps = report.get("steps", [])
        from services.replay_diagnostics import pause_category
        safe_reasons = []
        for item in report.get("pause_reasons", []):
            if not isinstance(item, dict):
                continue
            reason = str(item.get("reason", ""))
            safe_reasons.append({"step": item.get("step"), "reason": pause_category(reason)})
        return {"ok": True, "report": {
            "summary": report.get("summary", ""),
            "plan_revision": report.get("plan_revision"),
            "source_hash": report.get("source_hash"),
            "pause_reasons": safe_reasons,
            "steps": [{key: step.get(key) for key in
                       ("step", "action", "status", "verification", "capability")
                       if key in step} | ({"detail": "步骤失败，请检查本机运行报告"}
                                          if step.get("status") not in {"ok", "skipped"} else {})
                      for step in steps if isinstance(step, dict)],
        }}

    @router.post("/{rec_id}/export")
    def export_task_artifact(rec_id: str, body: ExportBody):
        """Create an opt-in portable Skill or Automation artifact."""
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        if body.kind not in {"skill", "automation", "bundle"}:
            return {"ok": False, "msg": "kind 必须是 skill、automation 或 bundle"}
        try:
            path = export_artifact(d, body.kind)
            return {"ok": True, "kind": body.kind, "path": str(path)}
        except Exception as exc:
            log.exception("导出任务产物失败: %s", rec_id)
            return {"ok": False, "msg": str(exc)}

    # ---------- 意图分析(移植 skill-recorder describer 的最小版) ----------

    @router.get("/{rec_id}/analysis")
    def get_analysis(rec_id: str):
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        path = d / "analysis.json"
        if not path.exists():
            return {"ok": True, "analysis": None}
        try:
            return {"ok": True, "analysis": json.loads(path.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return {"ok": True, "analysis": None}

    @router.post("/{rec_id}/analysis")
    def run_analysis(rec_id: str):
        """启发式生成意图+步骤概览;LLM 配置存在时仅润色标题(intent_analyst 三重防御)。"""
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        task_path = d / "task.json"
        if not task_path.exists():
            return {"ok": False, "msg": "没有可分析的任务"}
        from services.intent_analyst import analyze
        from services.llm_heal import get_healer
        healer = get_healer()
        result = analyze(json.loads(task_path.read_text(encoding="utf-8")),
                         llm_client=healer if healer.enabled else None)
        (d / "analysis.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "analysis": result}

    @router.put("/{rec_id}/analysis")
    def update_analysis(rec_id: str, body: dict):
        """人工编辑/批准分析结果;批准后意图进入导出的 Skill.md。"""
        d = _resolve(rec_id)
        if d is None:
            return {"ok": False, "msg": "找不到任务"}
        path = d / "analysis.json"
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "msg": "请先生成分析"}
        for key in ("title", "intent"):
            if isinstance(body.get(key), str) and body[key].strip():
                current[key] = body[key].strip()[:120]
        if "approved" in body:
            current["approved"] = bool(body["approved"])
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "analysis": current}

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
        if not body.approved:
            try:
                _disable_schedule(rec_id)
            except Exception as exc:
                return {"ok": False, "msg": f"无法停用定时任务，方案审批未更改: {exc}"}
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

    @router.post("/dev/seed-recording")
    def seed_recording(body: dict):
        """E2E fixture(移植 openskills seed-recording):注入原始事件流,
        走真实编译管道并返回 task.json——编译器测试完全数据驱动。
        仅在 TAGENT_E2E_FIXTURES=1 时存在,生产不可见。"""
        import os
        if os.environ.get("TAGENT_E2E_FIXTURES") != "1":
            return {"ok": False, "msg": "fixture API 未启用"}
        events = body.get("events")
        if not isinstance(events, list) or not events:
            return {"ok": False, "msg": "events 必填"}
        from compiler import Compiler
        with tempfile.TemporaryDirectory(dir=data_dir.parent) as staging:
            target = Path(staging) / "seed"
            target.mkdir()
            (target / "events.json").write_text(
                json.dumps({"steps": events}), encoding="utf-8")
            try:
                path = Compiler(target).compile()
                task = json.loads(Path(path).read_text(encoding="utf-8"))
            except Exception as exc:
                return {"ok": False, "msg": f"编译失败: {exc}"}
            return {"ok": True, "task": task}

    @router.get("/{rec_id}/vars")
    def get_variables(rec_id: str):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        return {"ok": True, "vars": store.variables(rec_id),
                "details": store.variables_with_examples(rec_id)}

    @router.put("/{rec_id}/vars/{step_index}")
    def set_variable(rec_id: str, step_index: int, var: str = ""):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        if store.set_variable(rec_id, step_index, var):
            try:
                _disable_schedule(rec_id)
            except Exception as exc:
                log.exception("计划变更后停用定时任务失败: %s", rec_id)
                return {"ok": False, "msg": f"变量已保存，但停用旧定时任务失败: {exc}"}
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
        if body is not None and (body.get("type") not in {"clipboard", "ui"}
                                 or (body.get("type") == "ui"
                                     and not str(body.get("control_name", "")).strip())):
            return {"ok": False, "msg": "变量来源无效"}
        if store.get_var_sources(rec_id).get(var_name) == body:
            return {"ok": True, "sources": store.get_var_sources(rec_id)}
        store.set_var_source(rec_id, var_name, body)
        try:
            _disable_schedule(rec_id)
        except Exception as exc:
            log.exception("变量来源变更后停用定时任务失败: %s", rec_id)
            return {"ok": False, "msg": f"变量来源已保存，但停用旧定时任务失败: {exc}"}
        review_path = data_dir / rec_id / "review.json"
        if review_path.exists():
            try:
                review = json.loads(review_path.read_text(encoding="utf-8"))
                if review.get("approved"):
                    review["approved"] = False
                    review["updated_at"] = __import__("time").time()
                    review.setdefault("history", []).append({
                        "action": "revoke", "reason": "variable_source_changed",
                        "ts": review["updated_at"],
                    })
                    tmp = review_path.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
                    tmp.replace(review_path)
            except (OSError, ValueError) as exc:
                return {"ok": False, "msg": f"变量来源已保存，但撤销旧审批失败: {exc}"}
        return {"ok": True, "sources": store.get_var_sources(rec_id)}

    # ---------- 阶段2.4 会话步骤 ----------

    @router.put("/{rec_id}/session/{step_index}")
    def set_session(rec_id: str, step_index: int, on: bool = True):
        if _resolve(rec_id) is None:
            return {"ok": False, "msg": "找不到任务"}
        store.set_session_step(rec_id, step_index, on)
        try:
            _disable_schedule(rec_id)
        except Exception as exc:
            log.exception("会话步骤变更后停用定时任务失败: %s", rec_id)
            return {"ok": False, "msg": f"会话步骤已保存，但停用旧定时任务失败: {exc}"}
        return {"ok": True}

    # ---------- 阶段4.2 定时调度 ----------

    @router.put("/{rec_id}/schedule")
    def set_schedule(rec_id: str, body: ScheduleBody):
        root = _resolve(rec_id)
        if root is None:
            return {"ok": False, "msg": "找不到任务"}
        try:
            task = json.loads((root / "task.json").read_text(encoding="utf-8"))
            plan_path = root / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else task.get("plan", {})
            review_path = root / "review.json"
            review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
            from services.run_context import build_run_context
            context = build_run_context(rec_id, task, plan, trigger="schedule", review=review)
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "msg": f"无法验证当前任务方案: {exc}"}
        if not context.review_approved:
            return {"ok": False, "msg": "请先审阅并批准当前方案，再启用定时执行"}
        sources = store.get_var_sources(rec_id)
        missing = [name for name in store.variables(rec_id)
                   if not isinstance(sources.get(name), dict)
                   or sources[name].get("type") not in {"clipboard", "ui"}]
        if missing:
            return {"ok": False, "msg": f"定时执行缺少变量来源: {', '.join(missing)}"}
        previous = scheduler.list_jobs().get(rec_id)
        try:
            scheduler.set_job(rec_id, body.model_dump())
            if autostart_sync:
                autostart_sync(bool(scheduler.list_jobs()))
            store.set_schedule(rec_id, body.model_dump())
        except Exception as exc:
            scheduler.set_job(rec_id, previous)
            try:
                if autostart_sync:
                    autostart_sync(bool(scheduler.list_jobs()))
            except Exception:
                log.exception("回滚 Windows 登录后台任务失败")
            return {"ok": False, "msg": f"无法保存定时任务或注册登录启动: {exc}"}
        return {"ok": True}

    @router.delete("/{rec_id}/schedule")
    def delete_schedule(rec_id: str):
        previous = scheduler.list_jobs().get(rec_id)
        scheduler.set_job(rec_id, None)
        try:
            if autostart_sync:
                autostart_sync(bool(scheduler.list_jobs()))
            store.set_schedule(rec_id, None)
        except Exception as exc:
            scheduler.set_job(rec_id, previous)
            try:
                if autostart_sync:
                    autostart_sync(bool(scheduler.list_jobs()))
            except Exception:
                log.exception("回滚 Windows 登录后台任务失败")
            return {"ok": False, "msg": f"无法注销登录启动任务: {exc}"}
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

    @router.post("/capabilities/compose")
    def compose_capabilities(body: ComposeBody):
        try:
            task = compose_task(data_dir, cap_registry.list(), body.capability_ids, body.name)
        except ValueError as ex:
            return {"ok": False, "msg": str(ex)}
        from datetime import datetime, timedelta
        stamp = datetime.now()
        rec_id = stamp.strftime("%Y%m%d_%H%M%S")
        while (data_dir / rec_id).exists():
            stamp += timedelta(seconds=1)
            rec_id = stamp.strftime("%Y%m%d_%H%M%S")
        task["source"] = rec_id
        meta = task.pop("composed_meta", {})
        task["plan"] = build_plan(task, session_steps=meta.get("session_steps", []))
        plan = task["plan"]
        meta.update({"name": body.name.strip(), "created_by": "capability_compose"})
        from services.task_versions import TaskVersions
        try:
            with tempfile.TemporaryDirectory(dir=data_dir.parent) as staging:
                target = Path(staging) / rec_id
                target.mkdir()
                (target / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
                (target / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
                (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                TaskVersions(target).snapshot(task, "capability_composed")
                target.replace(data_dir / rec_id)
        except OSError as ex:
            log.error("组合任务保存失败: %s", ex)
            return {"ok": False, "msg": "组合任务保存失败"}
        for cap_id in body.capability_ids:
            cap_registry.mark_used(cap_id)
        return {"ok": True, "task": {"id": rec_id, "name": body.name.strip(), "steps": len(task["steps"])}}

    @router.post("/capabilities/coverage")
    def capability_coverage(body: dict):
        """能力覆盖表(移植 flows.json Flow-First):哪些段可复用、哪些需录制。"""
        steps = body.get("steps")
        if not isinstance(steps, list) or not steps:
            return {"ok": False, "msg": "steps 必填"}
        from services.capability_compose import coverage
        return {"ok": True, "coverage": coverage(steps, cap_registry.list())}
    @router.post("/capabilities")
    def register_capability(body: CapRegisterBody):
        if body.postcondition and (body.postcondition.get("type") not in {"window", "text"}
                                   or not str(body.postcondition.get("value", "")).strip()):
            return {"ok": False, "msg": "完成条件须为窗口标题或屏幕文字，并填写目标文本"}
        d = _resolve(body.source_task)
        if d is None:
            return {"ok": False, "msg": "找不到源任务"}
        tf = d / "task.json"
        if tf.exists():
            try:
                source_task = json.loads(tf.read_text(encoding="utf-8"))
                total = len(source_task.get("steps", []))
            except Exception:
                total = 0
        else:
            total = 0
        start, end = body.step_range
        if not (0 <= start < end <= total):
            return {"ok": False, "msg": f"步骤区间越界（共 {total} 步）"}
        try:
            meta = store.load_meta(body.source_task)
            entry = cap_registry.register(
                body.name, body.source_task, [start, end], body.description,
                plan_source_hash(source_task), body.postcondition,
                source_task["steps"][start:end], meta.get("var_sources", {}),
                [index - start for index in meta.get("session_steps", [])
                 if start <= index < end])
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
