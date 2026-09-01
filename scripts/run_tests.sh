#!/usr/bin/env bash
# 运行纯单元测试（无后端/真实应用依赖）
set -e
cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -f "$PY" ]; then
    PY="python"
fi

UNIT_TESTS=(
    python/tests/test_capability_registry.py
    python/tests/test_compiler_semantic.py
    python/tests/test_eval_scorer.py
    python/tests/test_merge_warning.py
    python/tests/test_pii_guard.py
    python/tests/test_web_channel.py
)

for f in "${UNIT_TESTS[@]}"; do
    echo "== $f =="
    "$PY" "$f" 2>&1 | tail -n 2
done
echo "全部单元测试通过"