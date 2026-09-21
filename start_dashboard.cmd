@echo off
cd /d "%~dp0"
if not exist ".\.venv\Scripts\python.exe" (
  echo Local Python environment missing. See README.md for setup.
  pause
  exit /b 1
)
".\.venv\Scripts\python.exe" -B "whoop_dashboard.py" %*
if errorlevel 1 pause
