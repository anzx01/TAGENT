"""个人 agent 能力单测:推送/白名单/盯守/建议/记忆/连接器/队列/联动。"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_notifier_and_guard():
    from services import notifier, network_guard
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        assert notifier.notify("t", "b", base) is False  # 未配置
        notifier.save_config(base, {"url": "https://api.day.app/k/{title}/{body}", "enabled": True})
        hits = []

        def fake_urlopen(req, timeout=0):
            hits.append(req.full_url if hasattr(req, "full_url") else req)
            return "ok"

        assert notifier.notify("标题A", "内容B", base, urlopen=fake_urlopen)
        assert "api.day.app/k/%E6%A0%87%E9%A2%98A" in hits[0]
        # JSON POST 形式
        notifier.save_config(base, {"url": "https://hook.example/x", "enabled": True})
        assert notifier.notify("t", "b", base, urlopen=fake_urlopen)
        # 白名单拦截
        network_guard.save(base, {"enabled": True, "allow_hosts": ["api.day.app"]})
        assert notifier.notify("t", "b", base, urlopen=fake_urlopen) is False
        assert network_guard.outbound_allowed(base, "https://api.day.app/z")
        assert network_guard.outbound_allowed(base, "http://127.0.0.1:9/x")
        assert not network_guard.outbound_allowed(base, "https://hook.example/x")


def test_watchers():
    from services import watchers
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        flag = base / "flag.txt"
        assert not watchers.evaluate({"kind": "file_exists", "value": str(flag)})
        flag.write_text("x", encoding="utf-8")
        assert watchers.evaluate({"kind": "file_exists", "value": str(flag)})
        assert watchers.evaluate({"kind": "window_appears", "value": "Notepad"},
                                 window_appears=lambda v: v == "Notepad")
        assert not watchers.evaluate({"kind": "mail_arrived"})  # shortcut 恒 False
        item = watchers.upsert(base, "r1", {"kind": "file_exists", "value": str(flag)}, 1)
        assert watchers.load(base)[0]["rec_id"] == "r1"
        assert watchers.due({"last_fired": time.time() - 30, "cooldown_min": 1}) is False
        assert watchers.due({"last_fired": time.time() - 120, "cooldown_min": 1}) is True
        watchers.mark_fired(base, item["id"])
        assert watchers.load(base)[0]["last_fired"] > 0
        watchers.upsert(base, "r1", {"kind": "window_appears", "value": "X"})  # 同任务 upsert
        assert len(watchers.load(base)) == 1
        assert watchers.remove(base, "r1") and not watchers.load(base)


def test_suggestions():
    from services import suggestions
    now = time.time()
    with tempfile.TemporaryDirectory() as td:
        runs = Path(td)
        for i in range(2):
            (runs / f"1{i}-taskA_x.json").write_text(json.dumps({
                "rec_id": "taskA", "success": False, "finished_at": now - 100,
                "steps": [{"step": 2, "action": "click", "status": "failed"}]}), encoding="utf-8")
        (runs / "2-one_x.json").write_text(json.dumps({
            "rec_id": "taskB", "success": False, "finished_at": now - 100,
            "steps": [{"step": 0, "status": "failed"}]}), encoding="utf-8")
        (runs / "3-old_x.json").write_text(json.dumps({
            "rec_id": "taskC", "success": False, "finished_at": now - 20 * 86400,
            "steps": [{"step": 0, "status": "failed"}]}), encoding="utf-8")
        items = suggestions.mine(runs, now=now)
        assert len(items) == 1 and items[0]["rec_id"] == "taskA"
        assert items[0]["step"] == 2 and "第 3 步" in items[0]["message"]


def test_memory_and_connectors():
    from services import memory_store, connector_permissions
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        rule = memory_store.add(base, "下单前勾选发票")
        assert memory_store.context(base) .startswith("用户偏好规则")
        assert memory_store.forget(base, rule["id"]) and not memory_store.load(base)
        try:
            memory_store.add(base, "  ")
            raise AssertionError("空记忆应报错")
        except ValueError:
            pass
        for i in range(105):
            memory_store.add(base, f"规则{i}")
        assert len(memory_store.load(base)) == 100  # 有界淘汰

        assert connector_permissions.allowed(base, "outlook")  # 默认 act
        assert connector_permissions.set_permission(base, "outlook", "readonly")
        assert not connector_permissions.allowed(base, "outlook")
        assert not connector_permissions.set_permission(base, "nope", "readonly")
        assert connector_permissions.load(base)["files"] == "act"


def test_queue_and_chain():
    import server
    with tempfile.TemporaryDirectory():
        calls = []

        def fake_launch(rec_id, variables, from_step=0, trigger="manual", depth=0):
            calls.append(("launch", rec_id, trigger, depth))
            return None  # 模拟忙时启动失败,由调用方判定入队

        with patch.object(server, "_launch_replay", fake_launch):
            class P:
                paused = False
            with patch.object(server, "_player", P()):
                assert server._start_or_enqueue("t2", None) == "queued"
                assert server._start_or_enqueue("t3", None, trigger="schedule") == "queued"
                for i in range(server.QUEUE_CAP - 2):
                    server._replay_queue.append({"rec_id": f"q{i}", "variables": None,
                                                 "from_step": 0, "trigger": "manual", "depth": 0})
                assert server._start_or_enqueue("t4", None) == "failed"  # 队列满
            # 出队:bad 跳过,直到 started 返回
            calls.clear()
            server._replay_queue.clear()
            server._replay_queue.append({"rec_id": "bad", "variables": None,
                                         "from_step": 0, "trigger": "manual", "depth": 0})
            server._replay_queue.append({"rec_id": "t2", "variables": None,
                                         "from_step": 0, "trigger": "manual", "depth": 0})

            def launch_ok(rec_id, variables, from_step=0, trigger="manual", depth=0):
                calls.append(("launch", rec_id, trigger, depth))
                return rec_id if rec_id != "bad" else None

            with patch.object(server, "_launch_replay", launch_ok), \
                 patch.object(server, "_player", None):
                server._pump_replay_queue()
            assert [c[1] for c in calls] == ["bad", "t2"]
            # 非忙且启动失败 → failed 不排队
            with patch.object(server, "_launch_replay", lambda *a, **k: None), \
                 patch.object(server, "_player", None):
                assert server._start_or_enqueue("bad", None) == "failed"

        # 联动:meta.pipeline 按 on 匹配
        with tempfile.TemporaryDirectory() as td:
            with patch.object(server, "BASE_DIR", Path(td)), \
                 patch.object(server, "_start_or_enqueue",
                              lambda rid, v, fs=0, tr="manual", d=0: calls.append(("chain", rid, tr, d)) or "queued"):
                calls.clear()
                (Path(td) / "recordings").mkdir()
                for rec in ("a", "b", "c"):
                    (Path(td) / "recordings" / rec).mkdir()
                (Path(td) / "recordings" / "a" / "meta.json").write_text(json.dumps(
                    {"pipeline": {"steps": [
                        {"rec_id": "b", "on": "success"},
                        {"rec_id": "c", "on": "fail"},
                        {"rec_id": "missing", "on": "always"}]}}), encoding="utf-8")
                server._maybe_chain("a", {"success": True}, 0)
                assert [c[1] for c in calls] == ["b"]
                calls.clear()
                server._maybe_chain("a", {"success": False}, 0)
                assert [c[1] for c in calls] == ["c"]
                server._maybe_chain("a", {"success": True}, 5)  # 深度截断
    print("agent_features PASS")


def test_on_pause_notifies():
    import player as player_mod
    p = player_mod.Player.__new__(player_mod.Player)
    p._stopped = False
    p._paused = False
    p._resume = None
    p._resume_action = None
    p._waiting_step = None
    p._pause_reason = ""
    p._last_pause_reason = ""
    p._step_results = []
    p._pause_reasons = []
    seen = []
    p.on_pause = lambda step, reason: seen.append((step, reason))
    p.resume()
    t = threading.Thread(target=lambda: p._pause_for_human(2, "定位失败"), daemon=True)
    t.start()
    deadline = time.time() + 2
    while not p.paused and time.time() < deadline:
        time.sleep(0.01)
    assert seen == [(2, "定位失败")]
    p.stop()
    t.join(2)


def main():
    test_notifier_and_guard()
    test_watchers()
    test_suggestions()
    test_memory_and_connectors()
    test_queue_and_chain()
    test_on_pause_notifies()
    print("agent_features all PASS")


if __name__ == "__main__":
    main()
