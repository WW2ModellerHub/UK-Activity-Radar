@echo off
cd /d "%~dp0"
title UK Activity Radar - BODS Bus Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_buses.ps1"
echo.
pause
