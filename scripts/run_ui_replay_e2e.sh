#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -f "$PY" ] || ! "$PY" --version >/dev/null 2>&1; then
    PY="$(command -v python || true)"
fi
if [ -z "$PY" ]; then
    echo "[ERROR] Python 3.11+ was not found"
    exit 1
fi

"$PY" python/tests/test_ui_replay_e2e.py
