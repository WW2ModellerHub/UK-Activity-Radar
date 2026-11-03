$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot '.env'
Write-Host ''
Write-Host 'UK Activity Radar v1.4.0 - TomTom traffic + search setup' -ForegroundColor Cyan
Write-Host 'The TomTom key is used only by the local backend for:'
Write-Host '  - live traffic-flow tiles'
Write-Host '  - traffic incident tiles'
Write-Host '  - clicked-road speed / delay details'
Write-Host '  - UK road, junction, postcode and place search/geocoding'
Write-Host ''
Write-Host 'The key is stored only in this local .env file. It is not placed in browser JavaScript.'
Write-Host 'Use a TomTom developer key with Traffic API + Search API access.'
Write-Host ''
Write-Host 'TomTom API keys: https://my.tomtom.com/keys' -ForegroundColor Yellow
Write-Host ''
$secureKey = Read-Host 'TomTom API key' -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
}
if ([string]::IsNullOrWhiteSpace($key)) { throw 'API key cannot be blank.' }
$key = $key.Trim()

Write-Host ''
Write-Host 'Checking this key against TomTom Orbis Traffic...' -ForegroundColor Cyan
$testUrl = 'https://api.tomtom.com/maps/orbis/traffic/flow/raster/tile/7/63/41?apiVersion=2&style=light&tileSize=256'
$testHeaders = @{
    'Accept' = 'image/png'
    'TomTom-Api-Key' = $key
    'TomTom-Api-Version' = '2'
    'User-Agent' = 'UKActivityRadar/1.4.0-configurator'
}
try {
    $testResponse = Invoke-WebRequest -Uri $testUrl -Headers $testHeaders -UseBasicParsing -TimeoutSec 20
    if ([int]$testResponse.StatusCode -ne 200 -or $testResponse.Headers['Content-Type'] -notlike 'image/png*') {
        throw 'TomTom did not return a traffic tile.'
    }
}
catch {
    throw 'TomTom rejected this key. Copy the current API key from https://my.tomtom.com/keys and make sure Traffic API access is enabled.'
}

$lines = @()
if (Test-Path $envPath) { $lines = Get-Content -LiteralPath $envPath }
function Set-DotEnvValue([string]$name, [string]$value) {
    $script:lines = @($script:lines | Where-Object { $_ -notmatch ('^\s*' + [regex]::Escape($name) + '\s*=') })
    $script:lines += "$name=$value"
}
Set-DotEnvValue 'TRAFFIC_FLOW_ENABLED' 'true'
Set-DotEnvValue 'TOMTOM_TRAFFIC_API_KEY' $key
Set-DotEnvValue 'TOMTOM_TRAFFIC_BASE_URL' 'https://api.tomtom.com'
Set-DotEnvValue 'TRAFFIC_FLOW_CACHE_SECONDS' '45'
Set-DotEnvValue 'TRAFFIC_FLOW_CACHE_MAX_TILES' '512'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envPath, $lines, $utf8NoBom)
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'backup_credentials.ps1')) {
    & (Join-Path $PSScriptRoot 'backup_credentials.ps1') | Out-Null
}
$key = $null
Write-Host ''
Write-Host 'TomTom configuration saved.' -ForegroundColor Green
Write-Host 'Restart run.bat, then use Traffic -> Flow + incidents. Search will also prefer TomTom automatically.' -ForegroundColor Yellow
