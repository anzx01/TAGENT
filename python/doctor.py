"""TAGENT 本地运行诊断，输出可安全附在 issue 中的 JSON。"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import argparse
import zipfile
from pathlib import Path

from paths import BASE_DIR


def check() -> dict:
    recordings = BASE_DIR / "recordings"
    checks = {
        "python": {"ok": sys.version_info >= (3, 11), "value": sys.version.split()[0]},
        "recordings_dir": {"ok": recordings.exists() and os.access(recordings, os.W_OK), "value": str(recordings)},
        "tesseract": {"ok": bool(shutil.which("tesseract")), "value": shutil.which("tesseract") or "not found"},
        "win32gui": {"ok": importlib.util.find_spec("win32gui") is not None},
        "fastapi": {"ok": importlib.util.find_spec("fastapi") is not None},
    }
    interrupted = []
    if recordings.exists():
        for meta in recordings.glob("*/meta.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                if data.get("state") in {"recording", "stopping", "analyzing"}:
                    interrupted.append(meta.parent.name)
            except (OSError, ValueError):
                pass
    checks["interrupted_sessions"] = {"ok": not interrupted, "value": interrupted}
    return {"ok": all(v["ok"] for v in checks.values()), "checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--bundle", type=Path)
    args = parser.parse_args(); report = check()
    if args.bundle:
        args.bundle.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.bundle, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("doctor.json", json.dumps(report, ensure_ascii=False, indent=2))
            log_dir = BASE_DIR / "logs"
            for log in log_dir.glob("*.log") if log_dir.exists() else []:
                zf.write(log, f"logs/{log.name}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
