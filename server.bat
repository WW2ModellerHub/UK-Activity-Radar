@echo off
cd /d "%~dp0"
title UK Activity Radar Server v1.2.1
"%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info
set "RC=%ERRORLEVEL%"
echo.
echo UK Activity Radar v1.2.1 server stopped with exit code %RC%.
echo Leave this window open if an error is shown above.
pause
exit /b %RC%
