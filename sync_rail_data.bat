@echo off
setlocal
cd /d "%~dp0"
title UK Activity Radar - Rail Data Sync
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat first so the Python environment is created.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" sync_all_data.py
pause
