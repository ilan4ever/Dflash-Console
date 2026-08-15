[CmdletBinding()]
param(
    [string]$RemoteTheme = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $root ".env.admin"
function Read-EnvValue([string]$Name) {
    $environmentValue = [Environment]::GetEnvironmentVariable($Name)
    if ($environmentValue) { return $environmentValue }
    $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return (($line -split "=", 2)[1]).Trim().Trim("'").Trim('"')
}
$hostName = Read-EnvValue "HOSTINGER_SSH_HOST"
$userName = Read-EnvValue "HOSTINGER_SSH_USERNAME"
$port = Read-EnvValue "HOSTINGER_SSH_PORT"
$key = [Environment]::ExpandEnvironmentVariables((Read-EnvValue "HOSTINGER_SSH_PRIVATE_KEY_PATH"))
if (-not $hostName -or -not $userName -or -not $port -or -not $key) { throw "Missing Hostinger SSH settings in .env.admin." }
if (-not $RemoteTheme) { $RemoteTheme = Read-EnvValue "HOSTINGER_THEME_PATH" }
if (-not $RemoteTheme) { throw "Missing HOSTINGER_THEME_PATH (or -RemoteTheme). Set it in .env.admin." }
$updateRoot = Read-EnvValue "HOSTINGER_UPDATE_ROOT"
if (-not $updateRoot) { throw "Missing HOSTINGER_UPDATE_ROOT in .env.admin." }

$source = Join-Path $root "server\dflash-console-updates.php"
$remoteFile = "$RemoteTheme/dflash-console-updates.php"
& scp -i $key -P $port -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
    $source "${userName}@${hostName}:$remoteFile"
if ($LASTEXITCODE -ne 0) { throw "Could not upload the DFlash WordPress route." }

$localPhp = Join-Path ([IO.Path]::GetTempPath()) "dflash-console-updates.local-$([Guid]::NewGuid().ToString('N')).php"
try {
    $escaped = $updateRoot.Replace("\", "\\").Replace("'", "\'")
    Set-Content -LiteralPath $localPhp -Value "<?php`nreturn '$escaped';`n" -Encoding ascii
    & scp -i $key -P $port -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
        $localPhp "${userName}@${hostName}:$RemoteTheme/dflash-console-updates.local.php"
    if ($LASTEXITCODE -ne 0) { throw "Could not upload the private update-root config." }
} finally {
    Remove-Item $localPhp -Force -ErrorAction SilentlyContinue
}

$include = "require_once get_template_directory() . '/dflash-console-updates.php';"
$includeBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("`n$include`n"))
$remoteCommand = "grep -Fq 'dflash-console-updates.php' '$RemoteTheme/functions.php' || (printf '$includeBase64' | base64 -d >> '$RemoteTheme/functions.php')"
& ssh -i $key -p $port -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
    "$userName@$hostName" $remoteCommand
if ($LASTEXITCODE -ne 0) { throw "Could not enable the DFlash WordPress route." }
Write-Host "DFlash update endpoint deployed." -ForegroundColor Green
