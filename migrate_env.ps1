$ErrorActionPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$envPath = Join-Path $root '.env'
$parent = Split-Path $root -Parent

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

$previous = @(Get-ChildItem -LiteralPath $parent -Directory -Filter 'uk-activity-radar*' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -ne $root -and (Test-Path (Join-Path $_.FullName '.env')) } |
    Sort-Object LastWriteTime -Descending)

# If this is a fresh extraction, start with the newest real sibling configuration.
if (-not (Test-Path $envPath)) {
    foreach ($dir in $previous) {
        $candidate = Join-Path $dir.FullName '.env'
        if (Test-Path $candidate) {
            Copy-Item -LiteralPath $candidate -Destination $envPath -Force
            Write-Host ("Imported existing configuration from {0}." -f $dir.Name) -ForegroundColor Green
            break
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
    $keys = @(
        'NETWORK_RAIL_USERNAME','NETWORK_RAIL_PASSWORD','NETWORK_RAIL_ENABLED',
        'BODS_API_KEY','BODS_ENABLED',
        'NATIONAL_HIGHWAYS_API_KEY','NATIONAL_HIGHWAYS_ENABLED',
        'TOMTOM_TRAFFIC_API_KEY','TRAFFIC_FLOW_ENABLED',
        'AISSTREAM_API_KEY','AISSTREAM_ENABLED',
        'TFL_APP_KEY'
    )
    $changed = $false
    foreach ($key in $keys) {
        $needsValue = (-not $current.ContainsKey($key)) -or (Is-BlankEnvValue ([string]$current[$key]))
        if (-not $needsValue) { continue }
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
exit 0
