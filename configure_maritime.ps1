$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$envPath = Join-Path $PSScriptRoot '.env'

Write-Host ''
Write-Host 'UK Activity Radar - Maritime setup' -ForegroundColor Cyan
Write-Host 'AISStream provides free live AIS vessel positions via WebSocket.'
Write-Host 'Create a free API key here: https://aisstream.io/account'
Write-Host 'The key is stored only in your local .env file and is never sent to the browser.'
Write-Host ''
$secureKey = Read-Host 'Paste your AISStream API key (blank cancels)' -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
  $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
}
finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
}
if ([string]::IsNullOrWhiteSpace($key)) { exit 1 }
$key = $key.Trim()

$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
  throw 'Run run.bat once first so the Python environment is installed, then run maritime setup again.'
}

Write-Host ''
Write-Host 'Checking this key with AISStream...' -ForegroundColor Cyan
$previousProcessKey = [Environment]::GetEnvironmentVariable('AISSTREAM_API_KEY', 'Process')
try {
  $env:AISSTREAM_API_KEY = $key
  & $pythonPath (Join-Path $PSScriptRoot 'test_ais_feed.py') --validate-only
  $validationCode = $LASTEXITCODE
}
finally {
  if ($null -eq $previousProcessKey) {
    [Environment]::SetEnvironmentVariable('AISSTREAM_API_KEY', $null, 'Process')
  }
  else {
    [Environment]::SetEnvironmentVariable('AISSTREAM_API_KEY', $previousProcessKey, 'Process')
  }
}
if ($validationCode -ne 0) {
  $key = $null
  throw 'AISStream did not accept this subscription. Generate a current key at https://aisstream.io/account, or try again shortly if the provider is unavailable.'
}

if (-not (Test-Path $envPath)) {
  Copy-Item (Join-Path $PSScriptRoot '.env.example') $envPath
}

$lines = @(Get-Content -LiteralPath $envPath)
function Set-EnvLine([string]$name, [string]$value) {
  $script:lines = @($script:lines | Where-Object { $_ -notmatch ('^\s*' + [regex]::Escape($name) + '\s*=') })
  $script:lines += "$name=$value"
}
Set-EnvLine 'AISSTREAM_ENABLED' 'true'
Set-EnvLine 'AISSTREAM_API_KEY' $key
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($envPath, $lines, $utf8NoBom)
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'backup_credentials.ps1')) {
  & (Join-Path $PSScriptRoot 'backup_credentials.ps1') | Out-Null
}
$key = $null
Write-Host 'AISStream key saved locally in .env.' -ForegroundColor Green
