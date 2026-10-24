import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.replay_diagnostics import pause_category


def main():
    assert pause_category("等待目标窗口未出现: customer@example.com") == "等待目标窗口未出现"
    assert pause_category("能力完成条件未通过: 当前窗口不包含「订单号 1234」") == "能力完成条件未通过"
    assert pause_category("未知错误: private text") == "执行暂停"
    assert pause_category("") == ""
    import server
    import tempfile
    with tempfile.TemporaryDirectory() as td, patch.object(server, "BASE_DIR", Path(td)):
        assert server.get_startup_setting()["enabled"] is False
        with patch.object(server, "sync_windows_autostart") as sync:
            server._sync_startup(True)
        sync.assert_called_once_with(Path(td), False)
        with patch.object(server, "sync_windows_autostart"):
            saved = server.update_startup_setting(server.StartupSetting(enabled=True))
        assert saved["ok"] and server.get_startup_setting()["enabled"] is True
    fake = SimpleNamespace(
        steps=[{"action": "input_text", "text": "customer@example.com"}],
        _step_results=[], paused=True, _waiting_step=0,
        _pause_reason="能力完成条件未通过: 屏幕未找到「订单号 1234」",
    )
    with patch.object(server, "_player", fake):
        status = server.replay_status()
    assert status["step_desc"] == "input_text"
    assert status["pause_reason"] == "能力完成条件未通过"
    assert "customer@example.com" not in str(status)
    assert "订单号 1234" not in str(status)
    fake.steps = [{"action": "click"}] * 5
    fake._from_step = 3
    fake._waiting_step = None
    with patch.object(server, "_player", fake):
        resumed = server.replay_status()
    assert resumed["current_step"] == 3
    fake._step_results = [{"step": 3, "status": "ok"}]
    with patch.object(server, "_player", fake):
        resumed = server.replay_status()
    assert resumed["current_step"] == 4
    print("replay_diagnostics PASS")


if __name__ == "__main__":
    main()
