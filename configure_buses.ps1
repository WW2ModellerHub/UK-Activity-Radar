$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot '.env'
Write-Host ''
Write-Host 'UK Activity Radar v0.9 - BODS live bus setup' -ForegroundColor Cyan
Write-Host 'Paste the free Data Consumer API key from BODS Account settings.'
Write-Host 'It is stored only in this local .env file and is never sent to the browser.'
Write-Host ''
$key = Read-Host 'BODS API key'
if ([string]::IsNullOrWhiteSpace($key)) { throw 'API key cannot be blank.' }

$lines = @()
if (Test-Path $envPath) { $lines = Get-Content -LiteralPath $envPath }
function Set-DotEnvValue([string]$name, [string]$value) {
    $script:lines = @($script:lines | Where-Object { $_ -notmatch ('^\s*' + [regex]::Escape($name) + '\s*=') })
    $script:lines += "$name=$value"
}
Set-DotEnvValue 'BODS_ENABLED' 'true'
Set-DotEnvValue 'BODS_API_KEY' $key.Trim()
Set-DotEnvValue 'BODS_API_URL' 'https://data.bus-data.dft.gov.uk/api/v1/gtfsrtdatafeed/'
Set-DotEnvValue 'BODS_BULK_URL' 'https://data.bus-data.dft.gov.uk/avl/download/bulk_archive'
Set-DotEnvValue 'BODS_SX_ENABLED' 'true'
Set-DotEnvValue 'BODS_DISRUPTIONS_URL' 'https://data.bus-data.dft.gov.uk/api/v1/siri-sx'
Set-DotEnvValue 'BODS_CANCELLATIONS_URL' 'https://data.bus-data.dft.gov.uk/api/v1/siri-sx/cancellations'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envPath, $lines, $utf8NoBom)
Write-Host ''
Write-Host 'BODS configuration saved.' -ForegroundColor Green
Write-Host 'Restart run.bat. The app will try GTFS-RT first and automatically fall back to the public SIRI-VM bulk snapshot if needed.' -ForegroundColor Yellow
