#!/usr/bin/env bash
# 运行 E2E 测试（需后端 18899 运行中 + 目标应用：记事本/WPS/计算器）
set -e
cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -f "$PY" ]; then
    PY="python"
fi

E2E_TESTS=(
    python/tests/test_e2e.py
    python/tests/test_e2e_calc.py
    python/tests/test_e2e_edge.py
    python/tests/test_e2e_full.py
    python/tests/test_e2e_notepad_full.py
    python/tests/test_e2e_v1.py
    python/tests/test_e2e_wps.py
    python/tests/test_merge_e2e.py
    python/tests/test_record.py
    python/tests/test_replay_switch.py
    python/tests/test_selfheal_e2e.py
    python/tests/test_switch_wait.py
)

for f in "${E2E_TESTS[@]}"; do
    echo "== $f =="
    "$PY" "$f" 2>&1 | tail -n 4
done
echo "E2E 测试批次结束"