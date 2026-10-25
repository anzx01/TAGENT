"""加固修复回归:日志轮换/删除备份(回收站)/保留策略/Host+令牌/队列去重/WAL。"""
import json
import logging
import logging.handlers
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_logger_rotation():
    from logger import setup_logging
    root = setup_logging()
    handlers = [h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert handlers, "文件日志必须是轮换句柄"
    assert handlers[0].maxBytes > 0 and handlers[0].backupCount > 0


def test_delete_trash_backup():
    from services.recording_lifecycle import delete_recording, TRASH_KEEP
    from services.scheduler import Scheduler
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rec = root / "recordings" / "20261018_000001"
        rec.mkdir(parents=True)
        (rec / "task.json").write_text('{"steps":[]}', encoding="utf-8")
        scheduler = Scheduler(root / "schedules.json", lambda _: True)
        assert delete_recording(rec.name, rec, scheduler)["ok"]
        trash = root / "data" / "trash"
        zips = list(trash.glob("*.zip"))
        assert len(zips) == 1
        with zipfile.ZipFile(zips[0]) as zf:
            assert "task.json" in zf.namelist()
        # 备份失败 → 中止删除,数据仍在
        rec.mkdir(parents=True)
        (rec / "task.json").write_text("{}", encoding="utf-8")
        with patch("services.recording_lifecycle._backup_to_trash",
                   side_effect=OSError("disk full")):
            result = delete_recording(rec.name, rec, scheduler)
        assert not result["ok"] and "备份失败" in result["msg"] and rec.exists()
        # 回收站容量裁剪
        trash.mkdir(parents=True, exist_ok=True)
        for i in range(TRASH_KEEP + 3):
            (trash / f"x-{i:03d}.zip").write_bytes(b"x")
        rec.mkdir(parents=True, exist_ok=True)
        assert delete_recording(rec.name, rec, scheduler)["ok"]
        assert len(list(trash.glob("*.zip"))) <= TRASH_KEEP + 1


def test_retention():
    from services import retention
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        now = time.time()
        (base / "data").mkdir()
        (base / "data" / "retention.json").write_text(
            json.dumps({"enabled": True, "runs_keep_days": 30, "shots_keep": 2}), encoding="utf-8")
        runs = base / "runs"
        (runs / "screenshots" / "r1").mkdir(parents=True)
        old = runs / "100-old_x.json"
        new = runs / "200-new_x.json"
        old.write_text("{}", encoding="utf-8"); new.write_text("{}", encoding="utf-8")
        import os
        os.utime(old, (now - 40 * 86400, now - 40 * 86400))
        (runs / "screenshots" / "r1" / "a.png").write_bytes(b"x")
        os.utime(runs / "screenshots" / "r1" / "a.png", (now - 40 * 86400,) * 2)
        shots = base / "recordings" / "r2" / "screenshots"
        shots.mkdir(parents=True)
        for i in range(4):
            f = shots / f"s{i}.png"
            f.write_bytes(b"x")
            os.utime(f, (now - i, now - i))
        stats = retention.apply(base, now=now)
        assert not old.exists() and new.exists()
        assert stats["reports_removed"] == 1
        assert not (runs / "screenshots" / "r1" / "a.png").exists()
        assert len(list(shots.glob("*.png"))) == 2  # 只留最新 2 张
        # 关闭开关则不动
        (base / "data" / "retention.json").write_text('{"enabled": false}', encoding="utf-8")
        assert retention.apply(base, now=now)["enabled"] is False


def test_guard_host_and_token():
    import server
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as td:
        with patch.object(server, "BASE_DIR", Path(td)):
            client = TestClient(server.app)
            # 非法 Host → 403(DNS rebinding 防护)
            bad = TestClient(server.app)
            bad.headers.update({"Host": "evil.example.com"})
            assert bad.get("/status").status_code == 403
            # 正常 Host 的读操作不受影响
            assert client.get("/status").status_code == 200
            # 中断类端点需要令牌
            assert client.post("/shutdown").status_code == 401
            token = client.get("/api-token").json()["token"]
            assert token
            assert client.post("/shutdown",
                               headers={"X-TAGENT-Token": token}).status_code == 200
            assert client.post("/lifecycle/detach").status_code == 401
            assert client.post("/lifecycle/detach",
                               headers={"X-TAGENT-Token": token}).status_code == 200
            # 普通写操作不需要令牌
            assert client.put("/notify", json={"url": ""}).status_code == 200


def test_run_store_wal():
    from services import run_store
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "runs.db"
        conn = run_store._connect(db)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode.lower() == "wal"


def test_queue_dedupe():
    import server
    with patch.object(server, "_launch_replay", lambda *a, **k: None):
        class P:
            paused = False
            _rec_id = "taskX"
        with patch.object(server, "_player", P()):
            assert server._start_or_enqueue("taskX", None) == "queued"   # 执行中
            assert server._start_or_enqueue("taskY", None) == "queued"
            assert server._start_or_enqueue("taskY", None) == "queued"   # 已在队
            assert server._start_or_enqueue("taskZ", None) == "queued"
            assert len(server._replay_queue) == 2, list(server._replay_queue)
            assert {j["rec_id"] for j in server._replay_queue} == {"taskY", "taskZ"}


def main():
    test_logger_rotation()
    test_delete_trash_backup()
    test_retention()
    test_guard_host_and_token()
    test_run_store_wal()
    test_queue_dedupe()
    print("hardening PASS")


if __name__ == "__main__":
    main()
