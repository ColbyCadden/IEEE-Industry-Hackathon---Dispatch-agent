@echo off
REM One-time setup: creates a virtual environment and installs SUMO + OR-Tools. Safe to re-run.
REM The environment lives at %USERPROFILE%\.venvs\sumo, not in the repo: SUMO ships files with very
REM long names that break Windows' 260-character path limit when the repo is deep (e.g. in OneDrive).
setlocal
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.venvs\sumo"
if not exist "%VENV%\Scripts\python.exe" (
  echo Creating virtual environment in %VENV% ...
  python -m venv "%VENV%" || (echo Python 3.10+ is required & pause & exit /b 1)
)
"%VENV%\Scripts\python.exe" -m pip install -q --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install --no-cache-dir -r calgary3d\requirements.txt || (echo Install failed & pause & exit /b 1)
if not exist ".env" (
  echo ELEVENLABS_API_KEY=> ".env"
  echo Created .env - paste your ElevenLabs key after the = sign to enable the agent's voice.
)
echo.
echo Setup done. Start the simulation with start_all.bat
pause
