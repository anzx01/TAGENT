"""Keep scheduled work aligned with recording lifecycle operations."""
import shutil
import time
import zipfile
from pathlib import Path

TRASH_KEEP = 50  # 回收站最多保留的删除备份数


def _backup_to_trash(rec_dir: Path, rec_id: str) -> Path:
    """删除前把录制目录打包进 data/trash(误删可救回)。"""
    trash = rec_dir.parent.parent / "data" / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = trash / f"{rec_id}-{stamp}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(rec_dir.rglob("*")):
            if item.is_file():
                zf.write(item, item.relative_to(rec_dir).as_posix())
    backups = sorted(trash.glob("*.zip"))
    for old in backups[:-TRASH_KEEP]:
        old.unlink(missing_ok=True)
    return path


def delete_recording(rec_id: str, rec_dir: Path, scheduler,
                     sync_autostart=None, remove_tree=shutil.rmtree,
                     detach_capabilities=None) -> dict:
    previous = scheduler.list_jobs().get(rec_id)
    try:
        scheduler.set_job(rec_id, None)
        if previous is not None and sync_autostart:
            sync_autostart(bool(scheduler.list_jobs()))
        try:
            _backup_to_trash(rec_dir, rec_id)  # 备份失败则中止删除,宁可留任务不可丢数据
        except Exception as exc:
            return {"ok": False, "msg": f"删除前备份失败,已中止: {exc}"}
        remove_tree(rec_dir)
        if detach_capabilities:
            detach_capabilities(rec_id)
    except Exception as exc:
        if previous is not None:
            scheduler.set_job(rec_id, previous)
            try:
                if sync_autostart:
                    sync_autostart(bool(scheduler.list_jobs()))
            except Exception:
                return {"ok": False, "msg": f"删除失败且登录启动回滚失败: {exc}"}
        return {"ok": False, "msg": f"删除录制失败: {exc}"}
    return {"ok": True}
