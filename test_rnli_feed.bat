@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat once first so the Python environment is installed.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" test_rnli_feed.py
echo.
pause
