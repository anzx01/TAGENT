"""项目根目录解析：源码模式指向仓库根，PyInstaller 打包后指向 exe 所在目录"""
import os
import sys
import tempfile
from pathlib import Path

def _writable_data_dir() -> Path:
    """Choose a writable per-user data root for both installed and dev runs."""
    candidates = []
    explicit = os.environ.get("TAGENT_DATA_DIR", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / "TAGENT")
    candidates.append(Path(tempfile.gettempdir()) / "TAGENT")
    candidates.append(Path.home() / ".tagent")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="ascii")
            probe.unlink(missing_ok=True)
            return candidate
        except (OSError, PermissionError):
            continue
    raise RuntimeError("no writable TAGENT data directory found")


if getattr(sys, "frozen", False):
    # Installed resources can be read-only; never write beside the EXE.
    BASE_DIR = _writable_data_dir()
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
