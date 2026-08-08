@echo off
chcp 65001 >nul 2>&1
title 智能操作助手

:menu
cls
echo ========================================
echo   智能操作助手 控制面板
echo ========================================
echo.
echo   [1] 启动服务
echo   [2] 停止服务
echo   [3] 查看状态
echo   [4] 退出
echo.
set /p sel=请选择 (1-4): 

if "%sel%"=="1" goto start
if "%sel%"=="2" goto stop
if "%sel%"=="3" goto status
if "%sel%"=="4" exit
goto menu

:start
echo.
echo 正在清理旧进程...
taskkill /f /im python.exe >nul 2>&1
timeout /t 1 >nul
echo 正在启动后端服务...
cd /d "%~dp0"
.venv\Scripts\python.exe python\server.py
echo.
echo 服务已停止。
pause
goto menu

:stop
echo.
echo 正在停止服务...
taskkill /f /im python.exe >nul 2>&1
echo 已停止所有 Python 进程。
timeout /t 2 >nul
goto menu

:status
echo.
echo 检查服务状态...
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:18899/status' -TimeoutSec 3 -UseBasicParsing; $d = $r.Content | ConvertFrom-Json; Write-Host '服务: 运行中'; Write-Host '录制: '$d.recording; Write-Host '任务数: '$d.recordings.Count } catch { Write-Host '服务: 未运行' }"
echo.
pause
goto menu
