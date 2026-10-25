"""运行历史 SQLite 索引(移植 openskills ReplayRun/ReplayStepLog 表)。

runs/*.json 仍是事实源;本模块把它们镜像进可查询的 sqlite,
一条 SQL 回答"哪个任务哪个版本的哪次运行、哪步失败、重试几次"。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, rec_id TEXT, ts TEXT, trigger TEXT,
    success INTEGER, plan_revision INTEGER, source_hash TEXT,
    step_count INTEGER, pause_count INTEGER, summary TEXT, log_path TEXT);
CREATE INDEX IF NOT EXISTS idx_runs_rec ON runs(rec_id);
CREATE TABLE IF NOT EXISTS step_logs (
    run_id TEXT, seq INTEGER, step_id TEXT, action TEXT, status TEXT,
    method TEXT, detail TEXT, PRIMARY KEY (run_id, seq));
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")  # 回放线程写+查询并发读,避免 database is locked
    conn.executescript(_SCHEMA)
    return conn


def index_report(conn: sqlite3.Connection, report: dict, log_path: str) -> None:
    run_id = report.get("run_id") or f"{report.get('ts', '')}-{report.get('rec_id', '')}"
    conn.execute(
        "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, report.get("rec_id", ""), report.get("ts", ""), report.get("trigger", "manual"),
         1 if report.get("success") else 0, report.get("plan_revision"),
         report.get("source_hash", ""), len(report.get("steps", [])),
         len(report.get("pause_reasons", [])), report.get("summary", ""), log_path))
    conn.execute("DELETE FROM step_logs WHERE run_id = ?", (run_id,))
    for seq, step in enumerate(report.get("steps", [])):
        if not isinstance(step, dict):
            continue
        conn.execute("INSERT INTO step_logs VALUES (?,?,?,?,?,?,?)",
                     (run_id, seq, str(step.get("step_id", "")), step.get("action", ""),
                      str(step.get("status", "")), str(step.get("method", "")),
                      str(step.get("detail", ""))[:200]))


def index_all(runs_dir: Path, db_path: Path) -> int:
    """全量镜像 runs 目录;返回索引的运行数。"""
    count = 0
    conn = _connect(db_path)
    try:
        for path in sorted(Path(runs_dir).glob("*.json")):
            if path.name.endswith(".context.json"):
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(report, dict) and report.get("rec_id"):
                index_report(conn, report, path.name)
                count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def index_one(db_path: Path, report: dict, log_path: str) -> None:
    """单条报告入库(报告写盘后调用)。"""
    conn = _connect(db_path)
    try:
        index_report(conn, report, log_path)
        conn.commit()
    finally:
        conn.close()


def query(db_path: Path, rec_id: str | None = None, success: bool | None = None,
          limit: int = 50) -> list[dict]:
    sql = ("SELECT run_id, rec_id, ts, trigger, success, plan_revision, "
           "step_count, pause_count, summary FROM runs")
    where, params = [], []
    if rec_id:
        where.append("rec_id = ?")
        params.append(rec_id)
    if success is not None:
        where.append("success = ?")
        params.append(1 if success else 0)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rowid DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(zip(("run_id", "rec_id", "ts", "trigger", "success", "plan_revision",
                      "step_count", "pause_count", "summary"), row)) for row in rows]
