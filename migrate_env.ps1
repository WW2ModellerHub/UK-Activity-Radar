$ErrorActionPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$envPath = Join-Path $root '.env'
$parent = Split-Path $root -Parent
$vaultPath = if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Join-Path (Join-Path $env:LOCALAPPDATA 'UKActivityRadar') 'credentials.v1.json'
} else { $null }
if ($env:OS -eq 'Windows_NT') { Add-Type -AssemblyName System.Security }

function Read-EnvMap([string]$path) {
    $map = @{}
    if (-not (Test-Path $path)) { return $map }
    foreach ($rawLine in Get-Content -LiteralPath $path) {
        $line = $rawLine.TrimStart([char]0xFEFF)
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $map[$matches[1]] = $matches[2]
        }
    }
    return $map
}

function Is-BlankEnvValue([string]$value) {
    if ($null -eq $value) { return $true }
    $v = $value.Trim().Trim('"').Trim("'")
    return [string]::IsNullOrWhiteSpace($v)
}

function Read-ProtectedEnvMap([string]$path) {
    $map = @{}
    if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path)) { return $map }
    try {
        $document = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        if ($document.version -ne 1 -or $null -eq $document.values) { return $map }
        foreach ($property in $document.values.PSObject.Properties) {
            $cipherBytes = [Convert]::FromBase64String([string]$property.Value)
            $plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
                $cipherBytes,
                $null,
                [Security.Cryptography.DataProtectionScope]::CurrentUser
            )
            try {
                # Values are stored exactly as they appeared to the right of '='.
                $map[$property.Name] = [Text.Encoding]::UTF8.GetString($plainBytes)
            }
            finally {
                [Array]::Clear($plainBytes, 0, $plainBytes.Length)
            }
        }
    }
    catch {
        Write-Warning 'The protected credential backup could not be read; checking previous local builds instead.'
        return @{}
    }
    return $map
}

$keys = @(
    'NETWORK_RAIL_USERNAME','NETWORK_RAIL_PASSWORD','NETWORK_RAIL_ENABLED',
    'BODS_API_KEY','BODS_ENABLED',
    'NATIONAL_HIGHWAYS_API_KEY','NATIONAL_HIGHWAYS_ENABLED',
    'TOMTOM_TRAFFIC_API_KEY','TRAFFIC_FLOW_ENABLED',
    'AISSTREAM_API_KEY','AISSTREAM_ENABLED',
    'TFL_APP_KEY'
)
$protectedValues = Read-ProtectedEnvMap $vaultPath

# Extracted releases commonly live under Downloads while a Git checkout lives
# elsewhere. Search both locations, including the one nested directory created
# by many ZIP extractors, so an upgrade can recover an existing private config.
$searchRoots = @($parent)
$userProfile = [Environment]::GetFolderPath('UserProfile')
if (-not [string]::IsNullOrWhiteSpace($userProfile)) {
    $downloads = Join-Path $userProfile 'Downloads'
    if ((Test-Path -LiteralPath $downloads) -and $downloads -ne $parent) {
        $searchRoots += $downloads
    }
}

$candidateDirs = foreach ($searchRoot in $searchRoots) {
    $direct = @(Get-ChildItem -LiteralPath $searchRoot -Directory -Filter 'uk-activity-radar*' -ErrorAction SilentlyContinue)
    $direct
    foreach ($dir in $direct) {
        Get-ChildItem -LiteralPath $dir.FullName -Directory -Filter 'uk-activity-radar*' -ErrorAction SilentlyContinue
    }
}

$previous = @($candidateDirs |
    Where-Object { $_.FullName -ne $root -and (Test-Path (Join-Path $_.FullName '.env')) } |
    Sort-Object FullName -Unique |
    Sort-Object LastWriteTime -Descending)

# If this is a fresh extraction, prefer a clean template plus the protected
# vault. Fall back to copying the newest real sibling configuration when no
# protected backup exists yet.
$createdFromProtectedBackup = $false
if (-not (Test-Path $envPath)) {
    if ($protectedValues.Count -gt 0 -and (Test-Path -LiteralPath (Join-Path $root '.env.example'))) {
        Copy-Item -LiteralPath (Join-Path $root '.env.example') -Destination $envPath -Force
        $createdFromProtectedBackup = $true
        Write-Host 'Created local configuration from the protected credential backup.' -ForegroundColor Green
    }
    else {
        foreach ($dir in $previous) {
            $candidate = Join-Path $dir.FullName '.env'
            if (Test-Path $candidate) {
                Copy-Item -LiteralPath $candidate -Destination $envPath -Force
                Write-Host ("Imported existing configuration from {0}." -f $dir.Name) -ForegroundColor Green
                break
            }
        }
    }
}

# A prior buggy release could create a new .env containing TomTom/AIS but omit
# Network Rail (or vice versa). Fill only blank/missing setup values from older
# sibling configs; never overwrite a non-blank value in this build.
if (Test-Path $envPath) {
    $lines = @(Get-Content -LiteralPath $envPath)
    if ($lines.Count -gt 0) { $lines[0] = $lines[0].TrimStart([char]0xFEFF) }
    $current = Read-EnvMap $envPath
    $changed = $false
    foreach ($key in $keys) {
        # A real existing .env always wins. When this script just created the
        # file from the public template, restore saved enable/disable choices
        # as well as blank credential fields.
        $hasProtectedValue = $protectedValues.ContainsKey($key) -and -not (Is-BlankEnvValue ([string]$protectedValues[$key]))
        $needsValue = ($createdFromProtectedBackup -and $hasProtectedValue) -or (-not $current.ContainsKey($key)) -or (Is-BlankEnvValue ([string]$current[$key]))
        if (-not $needsValue) { continue }

        if ($hasProtectedValue) {
            $lines = @($lines | Where-Object { $_ -notmatch ('^\s*' + [regex]::Escape($key) + '\s*=') })
            $lines += "$key=$($protectedValues[$key])"
            $current[$key] = $protectedValues[$key]
            $changed = $true
            Write-Host ("Recovered {0} from the protected local backup." -f $key) -ForegroundColor DarkGray
            continue
        }

        foreach ($dir in $previous) {
            $candidate = Join-Path $dir.FullName '.env'
            $older = Read-EnvMap $candidate
            if ($older.ContainsKey($key) -and -not (Is-BlankEnvValue ([string]$older[$key]))) {
                $lines = @($lines | Where-Object { $_ -notmatch ('^\s*' + [regex]::Escape($key) + '\s*=') })
                $lines += "$key=$($older[$key])"
                $current[$key] = $older[$key]
                $changed = $true
                Write-Host ("Recovered {0} from {1}." -f $key, $dir.Name) -ForegroundColor DarkGray
                break
            }
        }
    }
    if ($changed) {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllLines($envPath, $lines, $utf8NoBom)
        Write-Host 'Merged missing feed credentials without overwriting current settings.' -ForegroundColor Green
    }
}

# Tell the launcher whether interactive Network Rail setup is still needed.
# An explicit NETWORK_RAIL_ENABLED=false is respected; otherwise both account
# fields must be present. No values are written to output.
if (-not (Test-Path -LiteralPath $envPath)) { exit 2 }
$finalValues = Read-EnvMap $envPath
$networkRailDisabled = $finalValues.ContainsKey('NETWORK_RAIL_ENABLED') -and ([string]$finalValues['NETWORK_RAIL_ENABLED']).Trim().Trim('"').Trim("'").ToLowerInvariant() -eq 'false'
if (-not $networkRailDisabled) {
    $missingNetworkRailUser = (-not $finalValues.ContainsKey('NETWORK_RAIL_USERNAME')) -or (Is-BlankEnvValue ([string]$finalValues['NETWORK_RAIL_USERNAME']))
    $missingNetworkRailPassword = (-not $finalValues.ContainsKey('NETWORK_RAIL_PASSWORD')) -or (Is-BlankEnvValue ([string]$finalValues['NETWORK_RAIL_PASSWORD']))
    if ($missingNetworkRailUser -or $missingNetworkRailPassword) { exit 2 }
}
exit 0
