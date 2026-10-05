@echo off
setlocal
cd /d "%~dp0"
title UK Activity Radar v1.0 Feed Health
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='http://127.0.0.1:8000/api/readiness'; try{$r=Invoke-RestMethod -TimeoutSec 5 $u}catch{Write-Host 'Radar server is not running. Start run.bat first.' -ForegroundColor Red; exit 1}; Write-Host ''; Write-Host ('OVERALL: ' + $r.overall.ToUpper()) -ForegroundColor Cyan; Write-Host ''; $r.sources | Select-Object name,health,mode,messages_seen,movements_mapped,@{n='last_update';e={if($_.age_seconds -eq $null){'never'}elseif($_.age_seconds -lt 60){[string]$_.age_seconds+'s ago'}else{[string][math]::Floor($_.age_seconds/60)+'m ago'}}},message | Format-Table -Wrap -AutoSize; Write-Host ''; Write-Host 'Green/ready feeds have proved data or a successful source response. STALE/ERROR/SETUP need attention.'"
echo.
pause
