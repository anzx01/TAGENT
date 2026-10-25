"""存储保留策略:运行报告/截图按期清理,磁盘不被历史数据淹没。

数据目录的清理是删除操作,只按明确的默认(90 天/每录制 50 张截图)执行,
可经 data/retention.json 关闭;一切删除都有日志。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULTS = {"enabled": True, "runs_keep_days": 90, "shots_keep": 50}


def _config(base_dir: Path) -> dict:
    cfg = dict(DEFAULTS)
    try:
        data = json.loads((Path(base_dir) / "data" / "retention.json").read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError):
        pass
    return cfg


def apply(base_dir: Path, now: float | None = None) -> dict:
    """执行一轮保留策略;返回清理统计。只按 mtime/数量判断,绝不碰基线与任务。"""
    from logger import get_logger
    log = get_logger("retention")
    cfg = _config(base_dir)
    stats = {"enabled": cfg["enabled"], "reports_removed": 0, "shots_removed": 0}
    if not cfg["enabled"]:
        return stats
    now = now if now is not None else time.time()
    cutoff = now - float(cfg["runs_keep_days"]) * 86400
    runs_dir = Path(base_dir) / "runs"
    if runs_dir.is_dir():
        for path in runs_dir.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    stats["reports_removed"] += 1
            except OSError:
                continue
        for rec_shots in (runs_dir / "screenshots").glob("*"):
            if not rec_shots.is_dir():
                continue
            for shot in rec_shots.iterdir():
                try:
                    if shot.is_file() and shot.stat().st_mtime < cutoff:
                        shot.unlink()
                        stats["shots_removed"] += 1
                except OSError:
                    continue
            try:
                rec_shots.rmdir()  # 目录空了才移除
            except OSError:
                pass
    keep = int(cfg["shots_keep"])
    for rec_shots in (Path(base_dir) / "recordings").glob("*/screenshots"):
        if not rec_shots.is_dir():
            continue
        shots = sorted((s for s in rec_shots.iterdir() if s.is_file()),
                       key=lambda s: s.stat().st_mtime, reverse=True)
        for shot in shots[keep:]:
            try:
                shot.unlink()
                stats["shots_removed"] += 1
            except OSError:
                continue
    if stats["reports_removed"] or stats["shots_removed"]:
        log.info("保留策略清理: 报告 %d 份,截图 %d 张", stats["reports_removed"], stats["shots_removed"])
    return stats
