@echo off
REM Start the Calgary 3D traffic sim as a DETACHED background process.
REM Unlike start_here.bat (which keeps a console window open), this launches
REM the server detached from any shell so it survives the parent exiting.
REM
REM Usage:  run_server_bg.bat
REM Then open http://localhost:8765/

setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "SUMO_BIN=%ROOT%.venv\Scripts\sumo.exe"
set "LOG=%ROOT%server.log"

if not exist "%PY%" (
  echo ERROR: venv missing at %PY%
  echo Create it:  python -m venv .venv
  pause
  exit /b 1
)

if not defined SUMO_BIN set "SUMO_BIN=%ROOT%.venv\Scripts\sumo.exe"

REM Check if already running on 8765.
netstat -ano | findstr ":8765" | findstr LISTENING >nul
if not errorlevel 1 (
  echo Already listening on port 8765 - nothing to do.
  exit /b 0
)

echo Starting server detached... log: %LOG%
start "Calgary3D" /min "%PY%" -u "%ROOT%calgary3d\server\server.py"

REM Wait for it to bind, then report.
set /a tries=0
:wait
timeout /t 3 >nul
set /a tries+=1
netstat -ano | findstr ":8765" | findstr LISTENING >nul
if not errorlevel 1 goto ok
if %tries% lss 12 goto fail
goto wait

:ok
echo.
echo Server is up:  http://localhost:8765/
start "" http://localhost:8765/
exit /b 0

:fail
echo Server did not start within ~36s. Last log lines:
if exist "%LOG%" powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 20"
pause
exit /b 1