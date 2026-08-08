@echo off
REM 智能操作助手 - Windows 快捷启动（内部调用 scripts/start_backend.sh + 打开 UI）
REM 完整桌面版请运行: bash scripts/start_app.sh

cd /d "%~dp0"

where bash >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Git Bash，请安装 Git for Windows
    pause
    exit /b 1
)

bash scripts/start_backend.sh

timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:18899/ui/"
echo.
echo 智能操作助手已启动！
echo 后端服务: http://127.0.0.1:18899
echo 完整桌面应用: bash scripts/start_app.sh
pause >nul