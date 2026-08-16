@echo off
REM Weekend summary of everything mover_watch logged during the week.
REM
REM Registered with Task Scheduler to run Saturday morning, after Friday's
REM nightly run has landed. Reads only the local JSON logs -- no API calls, no
REM database, so it cannot fail for anything but a missing file.
REM
REM Writes data\mover_watch\review\<date>.txt. If MOVER_WATCH_TELEGRAM_CHAT_ID
REM is set to a PRIVATE chat it also pushes the summary there; it will refuse
REM to send to TELEGRAM_CHAT_ID, which is the subscriber channel.

cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo [%date% %time%] mover_watch review >> "data\mover_watch\run.log"
%PY% tools\mover_watch.py --review >> "data\mover_watch\run.log" 2>&1
echo [%date% %time%] review exit=%ERRORLEVEL% >> "data\mover_watch\run.log"
