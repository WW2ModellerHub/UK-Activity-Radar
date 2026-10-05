@echo off
setlocal
cd /d "%~dp0"
title UK Activity Radar - BODS Test
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat once first so the Python environment is created.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" test_bods_live.py
echo.
pause
