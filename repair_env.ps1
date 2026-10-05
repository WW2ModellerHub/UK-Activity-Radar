$ErrorActionPreference = 'SilentlyContinue'
$envPath = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path $envPath)) { exit 0 }
$lines = @(Get-Content -LiteralPath $envPath)
$changed = $false
if ($lines.Count -gt 0) {
    $clean = $lines[0].TrimStart([char]0xFEFF)
    if ($clean -ne $lines[0]) { $lines[0] = $clean; $changed = $true }
}
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^\s*BODS_API_URL\s*=') {
        $value = ($lines[$i] -split '=',2)[1].Trim().Trim('"')
        if ($value -match '/api/v1/datafeed/?$') {
            $lines[$i] = 'BODS_API_URL=https://data.bus-data.dft.gov.uk/api/v1/gtfsrtdatafeed/'
            $changed = $true
        }
    }
}
if ($changed) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($envPath, $lines, $utf8NoBom)
    Write-Host 'Repaired legacy .env encoding/settings.' -ForegroundColor Yellow
}
exit 0
