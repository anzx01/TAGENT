#!/usr/bin/env bash
# 阶段5.4 打包：Tauri 安装包（内置 MSVC 环境配置，同 start_app.sh）
set -e
cd "$(dirname "$0")/.."

if [ -n "${USERPROFILE:-}" ]; then
    CARGO_USER_BIN="${USERPROFILE}/.cargo/bin"
elif command -v cygpath >/dev/null 2>&1 && [ -n "${USERPROFILE_WIN:-}" ]; then
    CARGO_USER_BIN="$(cygpath -u "${USERPROFILE_WIN}")/.cargo/bin"
else
    CARGO_USER_BIN=""
fi
if [ -d "$CARGO_USER_BIN" ]; then
    export PATH="$CARGO_USER_BIN:$PATH"
fi

# Always rebuild the bundled Python backend first. Otherwise Tauri can package
# a stale dist/tagent-server.exe and silently omit the latest replay fixes.
./scripts/build_sidecar.sh

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
fi

npx tauri build
echo "打包完成: src-tauri/target/release/bundle/ 下可找到安装包"
