@echo off
REM Starts the trading guard and restarts it automatically if it ever stops.
cd /d "%~dp0"
:loop
python guard.py
echo Guard stopped - restarting in 10 seconds (close this window to stop for good)...
timeout /t 10
goto loop
