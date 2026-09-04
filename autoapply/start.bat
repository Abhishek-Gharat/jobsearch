@echo off
rem Launch the AutoApply supervisor DETACHED so it survives closing this window.
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    pause
    exit /b 1
)
start "autoapply-supervisor" /min cmd /c "python supervisor.py --run >> logs\console.log 2>&1"
echo Supervisor launched detached (minimized window: autoapply-supervisor).
echo Monitor progress:   python supervisor.py --status
echo Live heartbeat:     powershell -Command "Get-Content heartbeat.log -Tail 5 -Wait"
timeout /t 3 >nul
