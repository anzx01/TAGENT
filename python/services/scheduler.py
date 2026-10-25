"""
轻量定时调度器（阶段4.2）
支持 daily（每日时分）与 interval（每隔 N 分钟），单一后台线程轮询。
用回调触发回放，不依赖外部库。
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from logger import get_logger

log = get_logger("scheduler")


class Scheduler:
    def __init__(self, tasks_db: Path, run_callback):
        """tasks_db: 调度配置 JSON；run_callback(rec_id) 执行任务回放"""
        self.tasks_db = tasks_db
        self.run_callback = run_callback
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._lock = threading.RLock()
        self._marks: set[tuple[str, str]] = set()

    # ---------- 配置持久化 ----------

    def load(self) -> dict[str, dict]:
        with self._lock:
            if self.tasks_db.exists():
                try:
                    return json.loads(self.tasks_db.read_text(encoding="utf-8"))
                except Exception:
                    return {}
            return {}

    def save(self, config: dict[str, dict]):
        with self._lock:
            if not config:
                self.tasks_db.unlink(missing_ok=True)
                return
            self.tasks_db.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.tasks_db.with_suffix(self.tasks_db.suffix + ".tmp")
            tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.tasks_db)

    def set_job(self, rec_id: str, sched: dict | None):
        with self._lock:
            config = self.load()
            if sched:
                if sched.get("kind") == "interval" and "next_run_at" not in sched:
                    sched["next_run_at"] = time.time() + max(1, int(sched.get("minutes", 60))) * 60
                config[rec_id] = sched
            else:
                config.pop(rec_id, None)
            self.save(config)

    def list_jobs(self) -> dict[str, dict]:
        with self._lock:
            return self.load()

    # ---------- 调度主循环 ----------

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        log.info("调度器启动")
        while not self._stop.wait(20):
            try:
                self._tick()
            except Exception:
                log.exception("调度 tick 失败")

    def _tick(self):
        """每 20s 检查一次：daily 按分钟对齐；interval 按"设定期大于间隔"触发。"""
        # Serialize trigger registration and its persisted update with set_job,
        # so a concurrent delete cannot be overwritten by a stale tick.
        with self._lock:
            now = datetime.now()
            config = self.load()
            for rec_id, sched in config.items():
                if not sched:
                    continue
                kind = sched.get("kind")
                if kind == "daily":
                    day = now.strftime("%Y%m%d")
                    due = (now.hour, now.minute) >= (sched.get("hour", 9), sched.get("minute", 0))
                    if due and sched.get("last_run_day") != day:
                        # 先落盘再触发:触发后崩溃只漏跑不重跑;启动失败则回滚保住重试资格
                        previous_day = sched.get("last_run_day")
                        sched["last_run_day"] = day
                        self.save(config)
                        if not self._maybe_fire(rec_id, ("daily", day)):
                            self._rollback(rec_id, "last_run_day", previous_day)
                    continue
                if kind == "interval":
                    minutes = max(1, int(sched.get("minutes", 60)))
                    due = float(sched.get("next_run_at", 0))
                    if not due:
                        sched["next_run_at"] = now.timestamp() + minutes * 60
                        self.save(config)
                    elif now.timestamp() >= due:
                        sched["next_run_at"] = now.timestamp() + minutes * 60
                        self.save(config)
                        if not self._maybe_fire(rec_id, ("interval", str(due))):
                            self._rollback(rec_id, "next_run_at", due)


    def _rollback(self, rec_id: str, field: str, previous):
        """启动失败时恢复触发资格(重新加载,避免覆盖并发删除)。"""
        with self._lock:
            config = self.load()
            if rec_id in config and config[rec_id].get(field) is not None:
                config[rec_id][field] = previous
                self.save(config)

    def _maybe_fire(self, rec_id: str, key: tuple[str, str]):
        if key in self._marks:
            return False
        log.info("触发定时任务 %s", rec_id)
        try:
            started = self.run_callback(rec_id)
            if not started:
                return False
            self._marks.add(key)
            if len(self._marks) > 2048:
                self._marks.clear()
            return True
        except Exception:
            log.exception("定时任务执行失败: %s", rec_id)
            return False
