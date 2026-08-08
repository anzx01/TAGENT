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
from pydantic import BaseModel

from logger import get_logger
from services.corrections import CorrectionStore
from services.scheduler import Scheduler
from services.task_store import TaskStore
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


def build_router(data_dir: Path, scheduler: Scheduler) -> APIRouter:
    """路由工厂：注入录制目录与调度器，避免全局状态循环依赖。"""
    router = APIRouter(prefix="/tasks", tags=["tasks"])
    store = TaskStore(data_dir)

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
            corr_n = 0
            cf = d / "corrections.json"
            if cf.exists():
                try:
                    corr_n = len(json.loads(cf.read_text(encoding="utf-8")))
                except Exception:
                    pass
            items.append({
                "id": rec_id,
                "name": meta.get("name", rec_id),
                "steps": count,
                "schedule": meta.get("schedule"),
                "vars": len(store.variables(rec_id)),
                "corrections": corr_n,
            })
        return {"ok": True, "tasks": items}

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