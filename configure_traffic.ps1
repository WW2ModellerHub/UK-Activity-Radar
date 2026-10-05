$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot '.env'
Write-Host ''
Write-Host 'UK Activity Radar v1.2.1 - TomTom traffic + search setup' -ForegroundColor Cyan
Write-Host 'The TomTom key is used only by the local backend for:'
Write-Host '  - live traffic-flow tiles'
Write-Host '  - traffic incident tiles'
Write-Host '  - clicked-road speed / delay details'
Write-Host '  - UK road, junction, postcode and place search/geocoding'
Write-Host ''
Write-Host 'The key is stored only in this local .env file. It is not placed in browser JavaScript.'
Write-Host 'Use a TomTom developer key with Traffic API + Search API access.'
Write-Host ''
Write-Host 'TomTom developer portal: https://developer.tomtom.com/' -ForegroundColor Yellow
Write-Host ''
$key = Read-Host 'TomTom API key'
if ([string]::IsNullOrWhiteSpace($key)) { throw 'API key cannot be blank.' }

$lines = @()
if (Test-Path $envPath) { $lines = Get-Content -LiteralPath $envPath }
function Set-DotEnvValue([string]$name, [string]$value) {
    $script:lines = @($script:lines | Where-Object { $_ -notmatch ('^\s*' + [regex]::Escape($name) + '\s*=') })
    $script:lines += "$name=$value"
}
Set-DotEnvValue 'TRAFFIC_FLOW_ENABLED' 'true'
Set-DotEnvValue 'TOMTOM_TRAFFIC_API_KEY' $key.Trim()
Set-DotEnvValue 'TOMTOM_TRAFFIC_BASE_URL' 'https://api.tomtom.com'
Set-DotEnvValue 'TRAFFIC_FLOW_CACHE_SECONDS' '45'
Set-DotEnvValue 'TRAFFIC_FLOW_CACHE_MAX_TILES' '512'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envPath, $lines, $utf8NoBom)
Write-Host ''
Write-Host 'TomTom configuration saved.' -ForegroundColor Green
Write-Host 'Restart run.bat, then use Traffic -> Flow + incidents. Search will also prefer TomTom automatically.' -ForegroundColor Yellow
