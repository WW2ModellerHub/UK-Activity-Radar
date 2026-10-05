@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_roads.ps1"
if errorlevel 1 (
  echo.
  echo Road setup was not completed.
  pause
  exit /b 1
)
echo.
echo Saved. Close and restart UK Activity Radar with run.bat.
pause
