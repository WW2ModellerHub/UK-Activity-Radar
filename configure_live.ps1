$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot '.env'
Write-Host ''
Write-Host 'UK Activity Radar - Network Rail Open Data setup' -ForegroundColor Cyan
Write-Host 'This updates only the Network Rail fields in .env; AIS, TomTom, BODS and road keys are preserved.'
Write-Host ''
$user = Read-Host 'Network Rail Open Data account email'
if ([string]::IsNullOrWhiteSpace($user)) { throw 'Email cannot be blank.' }
$secure = Read-Host 'Network Rail Open Data password' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
if ([string]::IsNullOrWhiteSpace($password)) { throw 'Password cannot be blank.' }

$lines = @()
if (Test-Path $envPath) { $lines = @(Get-Content -LiteralPath $envPath) }
elseif (Test-Path (Join-Path $PSScriptRoot '.env.example')) { $lines = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot '.env.example')) }
function Quote-DotEnv([string]$value) {
    $safe = $value.Replace('\','\\').Replace('"','\"').Replace("`r",'').Replace("`n",'')
    return '"' + $safe + '"'
}
function Set-DotEnvValue([string]$name, [string]$value) {
    $script:lines = @($script:lines | Where-Object { $_ -notmatch ('^\s*' + [regex]::Escape($name) + '\s*=') })
    $script:lines += "$name=$value"
}
Set-DotEnvValue 'NETWORK_RAIL_USERNAME' (Quote-DotEnv $user.Trim())
Set-DotEnvValue 'NETWORK_RAIL_PASSWORD' (Quote-DotEnv $password)
Set-DotEnvValue 'NETWORK_RAIL_ENABLED' 'true'
Set-DotEnvValue 'DEMO_MODE' 'false'
Set-DotEnvValue 'NETWORK_RAIL_HOST' 'publicdatafeeds.networkrail.co.uk'
Set-DotEnvValue 'NETWORK_RAIL_PORT' '61618'
Set-DotEnvValue 'NETWORK_RAIL_TRUST_TOPIC' '/topic/TRAIN_MVT_ALL_TOC'
Set-DotEnvValue 'NETWORK_RAIL_TD_TOPIC' '/topic/TD_ALL_SIG_AREA'
Set-DotEnvValue 'NETWORK_RAIL_VSTP_TOPIC' '/topic/VSTP_ALL'
Set-DotEnvValue 'TD_ENABLED' 'true'
Set-DotEnvValue 'VSTP_ENABLED' 'true'
Set-DotEnvValue 'AUTO_SYNC_SCHEDULE' 'true'
Set-DotEnvValue 'AUTO_SYNC_REFERENCE' 'true'
Set-DotEnvValue 'NETWORK_RAIL_CLIENT_ID' 'uk-activity-radar-dev'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envPath, $lines, $utf8NoBom)
Write-Host ''
Write-Host 'Network Rail credentials saved; other feed settings were preserved.' -ForegroundColor Green
Write-Host 'Restart run.bat. If a feed returns permission errors, check My Feeds on the Network Rail Open Data portal.' -ForegroundColor Yellow
