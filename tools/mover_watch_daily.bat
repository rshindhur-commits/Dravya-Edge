@echo off
REM Nightly movers check. Registered with Windows Task Scheduler; see the
REM schtasks line in tools/mover_watch.py's docstring.
REM
REM Runs against the laptop, not Render, deliberately: it shares Polygon quota
REM with the live scanner and there is no reason for a research job to compete
REM with the thing that places trades. It also means a failure here cannot
REM affect production.
REM
REM mover_watch.py exits quietly on weekends and holidays, so a daily trigger
REM is fine.

cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [%date% %time%] mover_watch starting >> "data\mover_watch\run.log"
%PY% tools\mover_watch.py --top 5 >> "data\mover_watch\run.log" 2>&1
echo [%date% %time%] mover_watch exit=%ERRORLEVEL% >> "data\mover_watch\run.log"
