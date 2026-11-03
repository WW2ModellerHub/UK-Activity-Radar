@echo off
cd /d "%~dp0"
title UK Activity Radar Server v1.5.0
"%~dp0.venv\Scripts\python.exe" "%~dp0app\network_environment.py"
if errorlevel 3 goto :blocked_network
if errorlevel 1 goto :preflight_failed
"%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info
set "RC=%ERRORLEVEL%"
echo.
echo UK Activity Radar v1.5.0 server stopped with exit code %RC%.
echo Leave this window open if an error is shown above.
pause
exit /b %RC%

:blocked_network
echo.
echo Live feeds cannot start from this restricted command environment.
echo Close this window and double-click run.bat in File Explorer.
echo Your API keys do not need to be changed.
pause
exit /b 3

:preflight_failed
echo Network environment preflight failed.
pause
exit /b 1
