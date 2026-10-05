$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$envPath = Join-Path $PSScriptRoot '.env'

Write-Host ''
Write-Host 'UK Activity Radar - Maritime setup' -ForegroundColor Cyan
Write-Host 'AISStream provides free live AIS vessel positions via WebSocket.'
Write-Host 'Create a free API key here: https://aisstream.io/account'
Write-Host 'The key is stored only in your local .env file and is never sent to the browser.'
Write-Host ''
$key = Read-Host 'Paste your AISStream API key (blank cancels)'
if ([string]::IsNullOrWhiteSpace($key)) { exit 1 }

if (-not (Test-Path $envPath)) {
  Copy-Item (Join-Path $PSScriptRoot '.env.example') $envPath
}

$content = Get-Content $envPath -Raw
function Set-EnvLine([string]$name, [string]$value) {
  $script:content = $script:content -replace "(?m)^$([regex]::Escape($name))=.*$", "$name=$value"
  if ($script:content -notmatch "(?m)^$([regex]::Escape($name))=") {
    $script:content = $script:content.TrimEnd() + "`r`n$name=$value`r`n"
  }
}
Set-EnvLine 'AISSTREAM_ENABLED' 'true'
Set-EnvLine 'AISSTREAM_API_KEY' $key.Trim()
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($envPath, $content, $utf8NoBom)
Write-Host 'AISStream key saved locally in .env.' -ForegroundColor Green
