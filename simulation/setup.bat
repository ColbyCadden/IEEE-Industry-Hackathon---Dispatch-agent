@echo off
REM One-time setup: creates .venv and installs SUMO + OR-Tools. Safe to re-run.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv || (echo Python 3.10+ is required & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -r calgary3d\requirements.txt || (echo Install failed & pause & exit /b 1)
if not exist ".env" (
  echo ELEVENLABS_API_KEY=> ".env"
  echo Created .env - paste your ElevenLabs key after the = sign to enable the agent's voice.
)
echo.
echo Setup done. Start the simulation with start_all.bat
pause
