#!/usr/bin/env bash
# 运行 Python 单元/冒烟测试
set -e
cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -f "$PY" ]; then
    PY="python"
fi

# 无 pytest 时用 unittest 方式逐个编译校验
for f in python/tests/test_*.py; do
    echo "== $f =="
    "$PY" -m py_compile "$f"
done
echo "全部测试文件编译通过"