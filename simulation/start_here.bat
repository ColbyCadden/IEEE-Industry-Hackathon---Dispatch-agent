@echo off
REM Launches the Calgary 3D traffic sim using this project's own Python venv.
REM The stock start_3d.bat assumes Python + SUMO are on your PATH; here they
REM live inside .venv, so we point at them explicitly.
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
set "SUMO_BIN=%~dp0.venv\Scripts\sumo.exe"

if not exist "%PY%" (
  echo Python venv not found at %PY%
  echo Create it with:  python -m venv .venv
  echo Then:            .venv\Scripts\python.exe -m pip install -r calgary3d\requirements.txt
  pause
  exit /b 1
)

start "Calgary3D server" "%PY%" -u calgary3d\server\server.py
timeout /t 15 >nul
start "" http://localhost:8765/
endlocal