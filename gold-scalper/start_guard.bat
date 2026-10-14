@echo off
REM Starts the trading guard and restarts it automatically if it ever stops.
REM Uses Python 3.12 if installed (the MetaTrader5 package does not support every Python version).
cd /d "%~dp0"
set PY=python
py -3.12 --version >nul 2>&1 && set PY=py -3.12
:loop
%PY% guard.py
echo Guard stopped - restarting in 10 seconds (close this window to stop for good)...
timeout /t 10
goto loop
