@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please follow README.md to install dependencies first.
  pause
  exit /b 1
)
echo ReproPilot: http://127.0.0.1:8765
".venv\Scripts\python.exe" run.py
pause
