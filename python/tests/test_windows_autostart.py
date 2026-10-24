"""Windows logon task generation and reconciliation tests."""
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import windows_autostart as autostart


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assert not autostart.preference(root)
        autostart.set_preference(root, True)
        assert autostart.preference(root)
        autostart.set_preference(root, False)
        assert not autostart.preference(root)
        calls = []

        def ok(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        executable = root / "server.exe"
        autostart.sync(root, True, executable, [], ok)
        test_name = "TAGENT Autostart Unit Test"
        assert calls[-1][1:4] == ["/Create", "/TN", autostart.TASK_NAME]
        script = (root / "autostart" / "start.vbs").read_text(encoding="utf-16")
        assert "127.0.0.1:18899/lifecycle" in script and str(executable) in script
        xml = ET.parse(root / "autostart" / "task.xml").getroot()
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        assert xml.findtext("t:Triggers/t:LogonTrigger/t:Enabled", namespaces=ns) == "true"
        assert xml.find("t:Principals/t:Principal/t:UserId", ns) is not None

        def failed(command, **kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="denied")

        try:
            autostart.sync(root, True, executable, [], failed)
            raise AssertionError("registration failure was ignored")
        except RuntimeError as exc:
            assert "denied" in str(exc)

        autostart.sync(root, False, runner=ok)
        assert calls[-1][1:4] == ["/Delete", "/TN", autostart.TASK_NAME]
        assert not (root / "autostart").exists()

        # 删除失败但任务本就不存在(本地化输出) → 视为成功并清理残留文件
        def missing(command, **kwargs):
            if command[1] == "/Query":
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="错误: 系统找不到指定的文件。")

        autostart.sync(root, True, executable, [], ok)
        autostart.sync(root, False, runner=missing)
        assert not (root / "autostart").exists()

        # 删除失败且任务仍然存在 → 报错
        def stuck(command, **kwargs):
            if command[1] == "/Query":
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="denied")

        try:
            autostart.sync(root, False, runner=stuck)
            raise AssertionError("delete failure was ignored")
        except RuntimeError as exc:
            assert "denied" in str(exc)
    print("windows_autostart PASS")


if __name__ == "__main__":
    main()
