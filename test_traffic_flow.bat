@echo off
cd /d "%~dp0"
title UK Activity Radar - Traffic Flow Test
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat once first so the local Python environment is created.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "%~dp0test_traffic_flow.py"
echo.
pause
