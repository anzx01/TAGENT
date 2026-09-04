#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

if command -v cygpath >/dev/null 2>&1; then
    WIN_CWD="$(cygpath -w "$(pwd)")"
else
    WIN_CWD="$(pwd -W 2>/dev/null || pwd)"
fi

VENV_PY="$WIN_CWD/.venv/Scripts/python.exe"
if [ ! -f "$VENV_PY" ] || ! "$VENV_PY" --version >/dev/null 2>&1; then
    if command -v python >/dev/null 2>&1; then
        VENV_PY="$(command -v python)"
    else
        echo "[ERROR] Python 3.11+ was not found."
        exit 1
    fi
fi

if curl -fsS --max-time 2 http://127.0.0.1:18899/status >/dev/null 2>&1; then
    echo "TAGENT backend is already running"
    exit 0
fi

mkdir -p logs
powershell.exe -NoProfile -WindowStyle Hidden -Command \
  "Start-Process -FilePath '$VENV_PY' -ArgumentList '$WIN_CWD/python/server.py' -WorkingDirectory '$WIN_CWD' -WindowStyle Hidden -RedirectStandardOutput '$WIN_CWD/logs/backend.out.log' -RedirectStandardError '$WIN_CWD/logs/backend.err.log'"

for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS --max-time 1 http://127.0.0.1:18899/status >/dev/null 2>&1; then
        echo "TAGENT backend is ready"
        exit 0
    fi
    sleep 1
done

echo "[ERROR] Backend did not become ready; see logs/backend.err.log"
exit 1
