"""Keep scheduled work aligned with recording lifecycle operations."""
import shutil
from pathlib import Path


def delete_recording(rec_id: str, rec_dir: Path, scheduler,
                     sync_autostart=None, remove_tree=shutil.rmtree,
                     detach_capabilities=None) -> dict:
    previous = scheduler.list_jobs().get(rec_id)
    try:
        scheduler.set_job(rec_id, None)
        if previous is not None and sync_autostart:
            sync_autostart(bool(scheduler.list_jobs()))
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
