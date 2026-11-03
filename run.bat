@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title UK Activity Radar Launcher v1.5.0

set "APP_URL=http://127.0.0.1:8000/"
set "HEALTH_URL=http://127.0.0.1:8000/api/health"
set "EXPECTED_VERSION=1.5.0"

echo ==============================================
echo       UK Activity Radar v1.5.0
echo       USEFUL + COOL + LIVE
echo ==============================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" was not found.
  echo Install Python 3.11 or newer from python.org, then run this file again.
  pause
  exit /b 1
)

rem Refuse the exact localhost discard proxy used by restricted development
rem shells. Do not clear arbitrary proxies: legitimate corporate proxies remain
rem supported and are inherited normally by the server.
py "%~dp0app\network_environment.py"
if errorlevel 3 goto :blocked_network
if errorlevel 1 goto :failed

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  py -m venv .venv
  if errorlevel 1 goto :failed
)

echo Installing/updating required packages...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto :failed

rem Never ship credentials in the ZIP. Recover/merge the user's local configuration.
echo Looking for your existing UK Activity Radar configuration...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0migrate_env.ps1"
set "CONFIG_RESULT=%ERRORLEVEL%"
if not "%CONFIG_RESULT%"=="0" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_live.ps1"
  if errorlevel 1 goto :failed
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0repair_env.ps1"
rem Keep an encrypted per-user backup so future clean copies restore credentials automatically.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0backup_credentials.ps1"

rem If this exact build is already running, just open it. A process that
rem inherited the discard proxy is rejected even though its web server answers.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $j=Invoke-RestMethod -TimeoutSec 2 '%HEALTH_URL%'; if($j.version -eq '%EXPECTED_VERSION%' -and $j.outbound_proxy_blocked){exit 3}; if($j.version -eq '%EXPECTED_VERSION%'){exit 0}else{exit 2} } catch { exit 1 }"
set "HEALTH_RESULT=%ERRORLEVEL%"
if "%HEALTH_RESULT%"=="3" goto :blocked_existing
if "%HEALTH_RESULT%"=="0" (
  echo UK Activity Radar v1.5.0 is already running. Opening it now...
  start "" "%APP_URL%"
  exit /b 0
)

rem Never terminate an arbitrary program just because it owns the configured port.
rem A second launcher can arrive while the first Radar server is still warming up:
rem wait for that server, or continue if its short-lived listener disappears.
echo Preparing local server...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(30); $sawListener=$false; while((Get-Date) -lt $deadline){ $owners=Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue ^| Select-Object -ExpandProperty OwningProcess -Unique; if(-not $owners){ exit 0 }; $sawListener=$true; try { $j=Invoke-RestMethod -TimeoutSec 2 '%HEALTH_URL%'; if($j.version -eq '%EXPECTED_VERSION%' -and $j.outbound_proxy_blocked){ exit 3 }; if($j.version -eq '%EXPECTED_VERSION%'){ exit 1 } } catch {}; Start-Sleep -Milliseconds 500 }; if($sawListener){ $owners=Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue ^| Select-Object -ExpandProperty OwningProcess -Unique; if($owners){ Write-Host 'Port 8000 is still in use by process ID(s):' ($owners -join ', '); exit 2 } }; exit 0"
set "PORT_RESULT=%ERRORLEVEL%"
if "%PORT_RESULT%"=="3" goto :blocked_existing
if "%PORT_RESULT%"=="1" (
  echo UK Activity Radar v1.5.0 has finished starting. Opening it now...
  start "" "%APP_URL%"
  exit /b 0
)
if "%PORT_RESULT%"=="2" (
  echo Port 8000 is already in use by another application.
  echo Close the process shown above, then run this launcher again.
  pause
  exit /b 1
)

rem Preserve expensive railway downloads/indexes from an earlier extracted build.
if not exist "data" mkdir "data"
if exist "..\UK-Activity-Radar-v1.3.2\data" (
  for %%G in (schedule.sqlite schedule-full.dat stations.json smart.json naptan_rail.json public_rail_geo.json rnli_stations.json naptan_stops.sqlite ferry_terminals.json ea_rain_stations.json) do (
    if not exist "data\%%G" if exist "..\UK-Activity-Radar-v1.3.2\data\%%G" copy /Y "..\UK-Activity-Radar-v1.3.2\data\%%G" "data\%%G" >nul
  )
)
for %%D in ("..\UK-Activity-Radar-v1.3.1" "..\uk-activity-radar-v1.2-private-configured" "..\uk-activity-radar-v1.1.5-private-configured" "..\uk-activity-radar-v1.1.4-private-configured" "..\uk-activity-radar-v1.1.4-feed-resilience" "..\uk-activity-radar-v1.1.4" "..\uk-activity-radar-v1.1.3-location-webcams" "..\uk-activity-radar-v1.1.3" "..\uk-activity-radar-v1.1.2" "..\uk-activity-radar-v1.1.2-live-boards-radar" "..\uk-activity-radar-v1.1.1" "..\uk-activity-radar-v1.1.1-map-first-repair" "..\uk-activity-radar-v1.1-recovered" "..\uk-activity-radar-v1.3-smart-places" "..\uk-activity-radar-v1.2" "..\uk-activity-radar-v1.2-live-world" "..\uk-activity-radar-v1.1" "..\uk-activity-radar-v1.1-live-traffic" "..\uk-activity-radar-v1.0" "..\uk-activity-radar-v0.9-rebuilt" "..\uk-activity-radar-v0.9" "..\uk-activity-radar-v0.9-live-tools" "..\uk-activity-radar-v0.8.4-fully-configured" "..\uk-activity-radar-v0.8.3-all-keys-configured" "..\uk-activity-radar-v0.8.2-configured" "..\uk-activity-radar-v0.8.1-bods-configured" "..\uk-activity-radar-v0.8" "..\uk-activity-radar-v0.8-reliability-search-images" "..\uk-activity-radar-v0.7.2" "..\uk-activity-radar-v0.7.1" "..\uk-activity-radar-v0.7" "..\uk-activity-radar-v0.7-maritime-rnli" "..\uk-activity-radar-v0.6-live-incidents" "..\uk-activity-radar-v0.5-live-buses" "..\uk-activity-radar-v0.4.1-live" "..\uk-activity-radar-v0.4-live") do (
  if exist "%%~D\data" (
    for %%G in (schedule.sqlite schedule-full.dat stations.json smart.json naptan_rail.json public_rail_geo.json rnli_stations.json naptan_stops.sqlite ferry_terminals.json ea_rain_stations.json) do (
      if not exist "data\%%G" if exist "%%~D\data\%%G" copy /Y "%%~D\data\%%G" "data\%%G" >nul
    )
  )
)

echo Starting local server...
start "UK Activity Radar Server v1.5.0" /min "%ComSpec%" /c call "%~dp0server.bat"

echo Waiting for the website to become ready...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$health='%HEALTH_URL%'; for($i=0;$i -lt 180;$i++){ try { $j=Invoke-RestMethod -TimeoutSec 2 $health; if($j.ok -and $j.version -eq '%EXPECTED_VERSION%'){ exit 0 } } catch {}; Start-Sleep -Milliseconds 500 }; exit 1"
if errorlevel 1 (
  echo.
  echo The v1.5.0 server did not answer within 90 seconds.
  echo Open the separate "UK Activity Radar Server v1.5.0" window for the exact error.
  echo.
  pause
  exit /b 1
)

echo Website ready. Opening the live map in your default browser...
start "" "%APP_URL%"
exit /b 0

:blocked_network
echo.
echo Live feeds cannot start from this restricted command environment.
echo Close this window and double-click run.bat in File Explorer.
echo Your API keys do not need to be changed.
pause
exit /b 3

:blocked_existing
echo.
echo A restricted UK Activity Radar server is already using port 8000.
echo Close that server, then double-click run.bat again from File Explorer.
echo Your API keys do not need to be changed.
pause
exit /b 3

:failed
echo.
echo Setup failed. Read the error above and try again.
pause
exit /b 1
