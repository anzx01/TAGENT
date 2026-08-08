#!/usr/bin/env bash
# 启动智能操作助手后端（FastAPI sidecar）
# PowerShell Start-Process 启动独立进程，脚本立即返回（已实测不挂起）
set -e
cd "$(dirname "$0")/.."

WIN_CWD="$(pwd -W 2>/dev/null || pwd)"
VENV_PY="$WIN_CWD/.venv/Scripts/python.exe"
if [ ! -f "$VENV_PY" ]; then
    VENV_PY="python"
fi

if curl -s -o /dev/null --max-time 1 http://127.0.0.1:18899/status; then
    echo "后端已在运行"
    exit 0
fi

mkdir -p logs
powershell.exe -NoProfile -WindowStyle Hidden -Command \
  "Start-Process -FilePath '$VENV_PY' -ArgumentList '$WIN_CWD/python/server.py' -WorkingDirectory '$WIN_CWD' -WindowStyle Hidden -RedirectStandardOutput '$WIN_CWD/logs/backend.out.log' -RedirectStandardError '$WIN_CWD/logs/backend.err.log'"
echo "后端已启动"