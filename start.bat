@echo off
setlocal
title TAGENT
cd /d "%~dp0"

set "PYTHON_EXE="
for /f "delims=" %%P in ('powershell -NoProfile -Command "(Get-Command python -ErrorAction SilentlyContinue).Source"') do set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
  echo [ERROR] Python was not found on PATH.
  echo Install Python 3.11+ and run: python -m pip install -r python\requirements.txt
  pause
  exit /b 1
)

echo Starting TAGENT backend...
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:18899/status | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 goto open_ui

start "TAGENT Backend" /min "%PYTHON_EXE%" "%CD%\python\server.py"

set /a WAIT_COUNT=0
:wait_backend
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:18899/status | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 goto open_ui
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 12 goto backend_failed
timeout /t 1 /nobreak >nul
goto wait_backend

:backend_failed
echo [ERROR] Backend did not become ready on http://127.0.0.1:18899/status
echo Check logs and Python dependencies, then try again.
pause
endlocal
exit /b 1

:open_ui
powershell -NoProfile -Command "try { Start-Process 'http://127.0.0.1:18899/ui/' -ErrorAction Stop } catch { }"
echo.
echo TAGENT is running.
echo Backend: http://127.0.0.1:18899
echo UI:      http://127.0.0.1:18899/ui/
echo.
echo Launcher complete. The backend stays running in the background.
endlocal
exit /b 0
