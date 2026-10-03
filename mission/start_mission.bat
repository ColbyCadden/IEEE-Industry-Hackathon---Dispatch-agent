@echo off
rem Opens 311 Mission Control at http://localhost:8800/ (close this window to stop it).
cd /d "%~dp0"
start "" http://localhost:8800/
python -m http.server 8800 --bind 127.0.0.1
