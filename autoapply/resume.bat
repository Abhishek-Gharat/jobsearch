@echo off
rem Resume = start. State lives in jobs.json/checkpoint.json, so resuming after a
rem crash, reboot, or OpenCode exit is identical to starting - just logged as 'resumed'.
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    pause
    exit /b 1
)
echo === Current state before resume ===
python supervisor.py --status
echo.
start "autoapply-supervisor" /min cmd /c "python supervisor.py --resume >> logs\console.log 2>&1"
echo Supervisor RESUMED detached (minimized window: autoapply-supervisor).
timeout /t 3 >nul
