"""Register the hidden backend at user logon while scheduled work exists."""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "TAGENT Scheduled Backend"


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


def sync(data_dir: Path, enabled: bool, executable: Path | None = None,
         args: list[str] | None = None, runner=subprocess.run,
         task_name: str = TASK_NAME) -> None:
    """Reconcile this user's logon task; raise on registration errors."""
    vbs_path, xml_path = _paths(data_dir)
    if not enabled:
        result = runner(["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
                        capture_output=True, text=True)
        if result.returncode and "cannot find" not in (result.stdout + result.stderr).lower():
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
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
