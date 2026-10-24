"""Register the hidden backend at user logon when explicitly enabled."""
import os
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "TAGENT Scheduled Backend"


def preference(data_dir: Path) -> bool:
    """User opt-in; missing or invalid settings mean no logon startup."""
    try:
        return json.loads((data_dir / "data" / "startup.json").read_text(encoding="utf-8")).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_preference(data_dir: Path, enabled: bool) -> None:
    path = data_dir / "data" / "startup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    tmp.replace(path)


def _vbs(executable: Path, args: list[str], script_path: Path, task_name: str) -> str:
    quote = lambda value: '"' + str(value).replace('"', '""') + '"'
    command = " ".join([quote(executable), *(quote(arg) for arg in args)])
    lines = [
        "On Error Resume Next",
        'Set http = CreateObject("MSXML2.XMLHTTP")',
        'http.Open "GET", "http://127.0.0.1:18899/lifecycle", False',
        "http.Send",
        "If Err.Number = 0 And http.Status = 200 Then WScript.Quit 0",
        "Err.Clear",
        f'If Not CreateObject("Scripting.FileSystemObject").FileExists({quote(executable)}) Then',
        f'  CreateObject("WScript.Shell").Run {quote("schtasks /Delete /TN " + quote(task_name) + " /F")}, 0, True',
        f'  CreateObject("Scripting.FileSystemObject").DeleteFile {quote(script_path)}, True',
        "  WScript.Quit 0",
        "End If",
        f'CreateObject("WScript.Shell").Run {quote(command)}, 0, False',
    ]
    return "\n".join(lines) + "\n"


def _task_xml(vbs_path: Path) -> str:
    command = escape(str(vbs_path))
    user = escape(os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", ""))
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>wscript.exe</Command><Arguments>"{command}"</Arguments></Exec></Actions>
</Task>'''


def _paths(data_dir: Path) -> tuple[Path, Path]:
    root = data_dir / "autostart"
    return root / "start.vbs", root / "task.xml"


def _schtasks(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """argv 已含 schtasks.exe 本身。schtasks 本地化输出(GBK 等)不能按 UTF-8
    严格解码,否则 reader 线程抛 UnicodeDecodeError 使 stdout/stderr 变成 None;
    mbcs+replace 保证必有可读字符串。"""
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="mbcs", errors="replace")


def _task_exists(runner, task_name: str) -> bool:
    return runner(["schtasks.exe", "/Query", "/TN", task_name],
                  capture_output=True, text=True).returncode == 0


def sync(data_dir: Path, enabled: bool, executable: Path | None = None,
         args: list[str] | None = None, runner=_schtasks,
         task_name: str = TASK_NAME) -> None:
    """Reconcile this user's logon task; raise on registration errors."""
    vbs_path, xml_path = _paths(data_dir)
    if not enabled:
        result = runner(["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
                        capture_output=True, text=True)
        # 删除失败但任务已不存在(输出是本地化文本,不能按字面匹配)视为成功
        if result.returncode and _task_exists(runner, task_name):
            raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                               or "schtasks delete failed")
        vbs_path.unlink(missing_ok=True)
        xml_path.unlink(missing_ok=True)
        try:
            vbs_path.parent.rmdir()
        except OSError:
            pass
        return
    executable = executable or Path(sys.executable)
    if args is None:
        args = [] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve().parents[1] / "server.py")]
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_path.write_text(_vbs(executable, args, vbs_path, task_name), encoding="utf-16")
    xml_path.write_text(_task_xml(vbs_path), encoding="utf-16")
    result = runner(["schtasks.exe", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
                    capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "schtasks registration failed")


def running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18899/lifecycle", timeout=0.5):
            return True
    except Exception:
        return False
