@echo off
REM Starts the sim server, then the agent team on a 5-minute loop (pipeline re-scrapes potholes too).
setlocal
cd /d "%~dp0"
set "SUMO_BIN=%~dp0.venv\Scripts\sumo.exe"
start "Calgary3D server" "%~dp0.venv\Scripts\python.exe" -u calgary3d\server\server.py
timeout /t 15 >nul
start "" http://localhost:8765/
"%~dp0.venv\Scripts\python.exe" -m agents.run_pipeline --apply --watch 300
