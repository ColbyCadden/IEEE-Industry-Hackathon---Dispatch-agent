@echo off
REM Starts the sim server, opens the browser, then runs the agent team every 5 minutes:
REM   311 scout/analyst -> priority list -> route planner -> traffic planner
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo .venv missing - running setup first...
  call setup.bat
)
set "SUMO_BIN=%~dp0.venv\Scripts\sumo.exe"
start "Calgary3D server" "%~dp0.venv\Scripts\python.exe" -u calgary3d\server\server.py
timeout /t 15 >nul
start "" http://localhost:8765/
"%~dp0.venv\Scripts\python.exe" -m agents.run_pipeline --apply --watch 300
