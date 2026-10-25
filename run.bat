@echo off
setlocal
title TAGENT Control Panel
cd /d "%~dp0"

:menu
cls
echo ========================================
echo          TAGENT Control Panel
echo ========================================
echo.
echo   [1] Start backend
echo   [2] Stop backend
echo   [3] Show status
echo   [4] Exit
echo.
set "sel="
set /p "sel=Select (1-4): " || exit /b 0
if "%sel%"=="1" goto start
if "%sel%"=="2" goto stop
if "%sel%"=="3" goto status
if "%sel%"=="4" exit /b 0
goto menu

:start
echo.
echo Starting backend...
call start.bat
goto menu

:stop
echo.
echo Stopping TAGENT backend...
powershell -NoProfile -Command "try { $tk = (Get-Content -Raw datapi_token.txt -ErrorAction Stop).Trim(); Invoke-RestMethod http://127.0.0.1:18899/shutdown -Method Post -Headers @{ 'X-TAGENT-Token' = $tk } -TimeoutSec 3 | Out-Null } catch { Write-Host 'Backend endpoint is not reachable; no process was force-killed.' }"
echo Done.
timeout /t 2 /nobreak >nul
goto menu

:status
echo.
powershell -NoProfile -Command "try { $r=Invoke-RestMethod http://127.0.0.1:18899/status -TimeoutSec 3; Write-Host ('Backend: running | Recording: ' + $r.recording + ' | Tasks: ' + $r.recordings.Count) } catch { Write-Host 'Backend: stopped' }"
echo.
pause
goto menu
