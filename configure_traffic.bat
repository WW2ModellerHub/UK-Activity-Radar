@echo off
cd /d "%~dp0"
title UK Activity Radar - TomTom Traffic + Search Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_traffic.ps1"
echo.
pause
