#!/usr/bin/env bash
# 网页通道 E2E：需先以 --remote-debugging-port=9222 启动浏览器并加载
# recordings/_web_test.html 测试页（见 run_web_e2e.py 头部说明）
set -e
cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -f "$PY" ]; then
    PY="python"
fi

"$PY" python/tests/run_web_e2e.py