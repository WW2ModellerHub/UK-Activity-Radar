@echo off
setlocal
cd /d "%~dp0"
title UK Activity Radar - Live Feed Test
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" test_network_rail.py
pause
