$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$envPath = Join-Path $PSScriptRoot '.env'

Write-Host ''
Write-Host 'UK Activity Radar - National Highways setup' -ForegroundColor Cyan
Write-Host 'The Road & Lane Closures API uses a free National Highways Developer Portal subscription key.'
Write-Host 'Register/subscribe here: https://developer.data.nationalhighways.co.uk/'
Write-Host ''
$key = Read-Host 'Paste your National Highways API subscription key (blank cancels)'
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
Set-EnvLine 'NATIONAL_HIGHWAYS_ENABLED' 'true'
Set-EnvLine 'NATIONAL_HIGHWAYS_API_KEY' $key.Trim()
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($envPath, $content, $utf8NoBom)
Write-Host 'National Highways key saved locally in .env.' -ForegroundColor Green
