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
        self._lock = threading.Lock()
        self._marks: set[tuple[str, str]] = set()

    # ---------- 配置持久化 ----------

    def load(self) -> dict[str, dict]:
        if self.tasks_db.exists():
            try:
                return json.loads(self.tasks_db.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save(self, config: dict[str, dict]):
        config = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                  for k, v in config.items()}
        if not config:
            self.tasks_db.unlink(missing_ok=True)
            return
        self.tasks_db.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_job(self, rec_id: str, sched: dict | None):
        with self._lock:
            config = self.load()
            if sched:
                config[rec_id] = sched
            else:
                config.pop(rec_id, None)
            self.save(config)

    def list_jobs(self) -> dict[str, dict]:
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
        now = datetime.now()
        config = self.load()
        for rec_id, sched in config.items():
            if not sched:
                continue
            kind = sched.get("kind")
            if kind == "daily":
                if (now.hour, now.minute) == (sched.get("hour", 9), sched.get("minute", 0)):
                    key = ("daily", now.strftime("%Y%m%d%H%M"))
                    self._maybe_fire(rec_id, key)
                continue
            if kind == "interval":
                minutes = max(1, int(sched.get("minutes", 60)))
                start = float(sched.get("_start_ts", now.timestamp()))
                if now.timestamp() - start >= minutes * 60:
                    sched["_start_ts"] = now.timestamp()
                    key = ("interval", now.strftime("%Y%m%d%H%M%S"))
                    self._maybe_fire(rec_id, key)
                    self.save(config)

    def _maybe_fire(self, rec_id: str, key: tuple[str, str]):
        if key in self._marks:
            return
        self._marks.add(key)
        if len(self._marks) > 2048:
            self._marks.clear()
        log.info("触发定时任务 %s", rec_id)
        try:
            self.run_callback(rec_id)
        except Exception:
            log.exception("定时任务执行失败: %s", rec_id)