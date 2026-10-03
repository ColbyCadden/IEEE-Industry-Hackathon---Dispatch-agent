@echo off
REM Starts the live SUMO server and opens the 3D city in your browser.
REM Requires Python 3.14 with the packages from requirements.txt installed.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on your PATH.
  echo Install Python 3.14 from https://www.python.org/downloads/
  echo then run:  pip install sumo
  pause
  exit /b 1
)

start "Calgary3D server" python -u server\server.py
timeout /t 12 >nul
start "" http://localhost:8765/
