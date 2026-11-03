$ErrorActionPreference = 'Stop'

# Keep a recoverable copy of the user's private feed setup outside the checkout.
# Windows DPAPI encrypts every value for the current Windows account, so the
# resulting JSON file is not a plaintext .env and cannot be decrypted by a
# different account or copied to another computer for use there.
if ($env:OS -ne 'Windows_NT') { exit 0 }
Add-Type -AssemblyName System.Security

$envPath = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) { exit 0 }
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Write-Warning 'The protected credential backup was skipped because LOCALAPPDATA is unavailable.'
    exit 0
}

$vaultDir = Join-Path $env:LOCALAPPDATA 'UKActivityRadar'
$vaultPath = Join-Path $vaultDir 'credentials.v1.json'
$credentialKeys = @(
    'NETWORK_RAIL_USERNAME','NETWORK_RAIL_PASSWORD','NETWORK_RAIL_ENABLED',
    'BODS_API_KEY','BODS_ENABLED',
    'NATIONAL_HIGHWAYS_API_KEY','NATIONAL_HIGHWAYS_ENABLED',
    'TOMTOM_TRAFFIC_API_KEY','TRAFFIC_FLOW_ENABLED',
    'AISSTREAM_API_KEY','AISSTREAM_ENABLED',
    'TFL_APP_KEY'
)

function Read-EnvMap([string]$path) {
    $map = @{}
    if (-not (Test-Path -LiteralPath $path)) { return $map }
    foreach ($rawLine in Get-Content -LiteralPath $path) {
        $line = $rawLine.TrimStart([char]0xFEFF)
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            # Keep the right-hand side byte-for-byte, including dotenv quotes.
            $map[$matches[1]] = $matches[2]
        }
    }
    return $map
}

function Is-BlankEnvValue([string]$value) {
    if ($null -eq $value) { return $true }
    return [string]::IsNullOrWhiteSpace($value.Trim().Trim('"').Trim("'"))
}

function Read-ProtectedMap([string]$path) {
    $map = @{}
    if (-not (Test-Path -LiteralPath $path)) { return $map }
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
                $map[$property.Name] = [Text.Encoding]::UTF8.GetString($plainBytes)
            }
            finally {
                [Array]::Clear($plainBytes, 0, $plainBytes.Length)
            }
        }
    }
    catch {
        Write-Warning 'The existing protected credential backup could not be read; it will be replaced from the current local configuration.'
        return @{}
    }
    return $map
}

$temporaryPath = $null
$stage = 'reading the current configuration'
try {
    $values = Read-ProtectedMap $vaultPath
    $current = Read-EnvMap $envPath
    foreach ($key in $credentialKeys) {
        if ($current.ContainsKey($key) -and -not (Is-BlankEnvValue ([string]$current[$key]))) {
            $values[$key] = [string]$current[$key]
        }
    }

    $stage = 'encrypting values'
    $encryptedValues = [ordered]@{}
    foreach ($key in $credentialKeys) {
        if (-not $values.ContainsKey($key) -or (Is-BlankEnvValue ([string]$values[$key]))) { continue }
        $plainBytes = [Text.Encoding]::UTF8.GetBytes([string]$values[$key])
        try {
            $cipherBytes = [Security.Cryptography.ProtectedData]::Protect(
                $plainBytes,
                $null,
                [Security.Cryptography.DataProtectionScope]::CurrentUser
            )
            $encryptedValues[$key] = [Convert]::ToBase64String($cipherBytes)
        }
        finally {
            [Array]::Clear($plainBytes, 0, $plainBytes.Length)
        }
    }
    if ($encryptedValues.Count -eq 0) { exit 0 }

    $stage = 'writing the protected file'
    New-Item -ItemType Directory -Path $vaultDir -Force | Out-Null
    $payload = [ordered]@{
        version = 1
        protectedFor = 'CurrentUser'
        savedAtUtc = [DateTime]::UtcNow.ToString('o')
        values = $encryptedValues
    }
    $json = $payload | ConvertTo-Json -Depth 4
    $temporaryPath = "$vaultPath.$PID.tmp"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($temporaryPath, $json, $utf8NoBom)

    $stage = 'restricting file permissions'
    # Lock down the temporary file before it replaces the previous vault.
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemUser = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($currentUser, 'FullControl', 'Allow')))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemUser, 'FullControl', 'Allow')))
    Set-Acl -LiteralPath $temporaryPath -AclObject $acl

    $stage = 'replacing the protected file'
    Move-Item -LiteralPath $temporaryPath -Destination $vaultPath -Force

    Write-Host 'Protected local credential backup updated.' -ForegroundColor DarkGray
}
catch {
    Write-Warning ("The protected credential backup could not be updated while {0}; the local .env remains unchanged." -f $stage)
}
finally {
    if (-not [string]::IsNullOrWhiteSpace($temporaryPath) -and (Test-Path -LiteralPath $temporaryPath)) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}
exit 0
