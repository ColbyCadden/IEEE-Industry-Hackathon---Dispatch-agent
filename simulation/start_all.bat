@echo off
REM Starts the sim server, opens the browser, then runs the agent team every 5 minutes:
REM   311 scout/analyst -> priority list -> route planner -> traffic planner
setlocal
cd /d "%~dp0"
REM Prefer the short-path environment from setup.bat; fall back to an older in-repo .venv.
set "VENV=%USERPROFILE%\.venvs\sumo"
if not exist "%VENV%\Scripts\sumo.exe" if exist ".venv\Scripts\sumo.exe" set "VENV=%~dp0.venv"
if not exist "%VENV%\Scripts\sumo.exe" (
  echo SUMO environment missing - running setup first...
  call setup.bat
  set "VENV=%USERPROFILE%\.venvs\sumo"
)
set "SUMO_BIN=%VENV%\Scripts\sumo.exe"
start "Calgary3D server" "%VENV%\Scripts\python.exe" -u calgary3d\server\server.py
timeout /t 15 >nul
start "" http://localhost:8765/
"%VENV%\Scripts\python.exe" -m agents.run_pipeline --apply --watch 300
