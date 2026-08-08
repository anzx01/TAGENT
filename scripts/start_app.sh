#!/usr/bin/env bash
# 启动完整应用（Tauri 壳 + Python 后端自动拉起）
# 内置 MSVC/SDK 环境配置（原 build_tauri.ps1 逻辑），避免 vswhere 未注册时报错
set -e
cd "$(dirname "$0")/.."

# ---- MSVC/SDK 环境（路径不存在时降级为系统 PATH，仅提示）----
MSVC_BASE="/c/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Tools/MSVC"
SDK_BASE="/c/Program Files (x86)/Windows Kits/10"
SDK_VER="10.0.26100.0"

MSVC_DIR=$(ls -d "$MSVC_BASE"/*/ 2>/dev/null | head -1)
if [ -n "$MSVC_DIR" ]; then
    MSVC_DIR=${MSVC_DIR%/}
    export PATH="$MSVC_DIR/bin/Hostx64/x64:$SDK_BASE/bin/$SDK_VER/x64:$PATH"
    export INCLUDE="$MSVC_DIR/include;$SDK_BASE/Include/$SDK_VER/um;$SDK_BASE/Include/$SDK_VER/ucrt;$SDK_BASE/Include/$SDK_VER/shared;$SDK_BASE/Include/$SDK_VER/winrt;$SDK_BASE/Include/$SDK_VER/cppwinrt"
    export LIB="$MSVC_DIR/lib/x64;$SDK_BASE/Lib/$SDK_VER/um/x64;$SDK_BASE/Lib/$SDK_VER/ucrt/x64"
    echo "[env] MSVC: $(basename "$MSVC_DIR") | SDK: $SDK_VER"
else
    echo "[env] 未找到 MSVC BuildTools，尝试使用系统编译链"
fi

# 先确保后端在运行
./scripts/start_backend.sh

# 启动 Tauri 开发模式
npx tauri dev