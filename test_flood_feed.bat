@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat first so the local Python environment is created.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" test_environment_agency.py
pause
