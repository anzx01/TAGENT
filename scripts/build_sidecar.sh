#!/usr/bin/env bash
# 阶段5.4：Python 后端打包为独立 exe（PyInstaller），供不带 Python 环境的机器直接运行
# 产物: dist/server/tagent-server.exe
# 说明: 打包后运行时需 --port 默认 18899；Tauri 启动时若检测到 dist 版 exe 优先使用它。
set -e
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
    echo "[错误] 请先创建 .venv: uv venv && uv pip install -r python/requirements.txt -r python/requirements-build.txt"
    exit 1
fi

# 构建依赖（仅打包含 pyinstaller，未装则提示）
".venv/Scripts/python.exe" -c "import PyInstaller" 2>/dev/null || {
    echo "安装 pyinstaller ..."
    .venv/Scripts/python.exe -m pip install pyinstaller
}

mkdir -p dist

# 排除无需打包的大模块可后续按需调优；--collect-all 收集 win32api/uiautomation 的隐藏依赖
.venv/Scripts/python.exe -m PyInstaller \
    --name tagent-server \
    --onefile \
    --collect-all uiautomation \
    --collect-all win32com \
    --paths python \
    --add-data "python;python" \
    python/server.py

echo "完成: dist/tagent-server.exe"
echo "运行: dist/tagent-server.exe  (监听 127.0.0.1:18899)"