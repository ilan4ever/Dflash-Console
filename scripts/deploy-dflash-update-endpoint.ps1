[CmdletBinding()]
param(
    [string]$RemoteTheme = "/home/u840646150/domains/onevoiceai.in/public_html/wp-content/themes/one-voice-design"
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

$source = Join-Path $root "server\dflash-console-updates.php"
$remoteFile = "$RemoteTheme/dflash-console-updates.php"
& scp -i $key -P $port -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
    $source "${userName}@${hostName}:$remoteFile"
if ($LASTEXITCODE -ne 0) { throw "Could not upload the DFlash WordPress route." }

$include = "require_once get_template_directory() . '/dflash-console-updates.php';"
$includeBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("`n$include`n"))
$remoteCommand = "grep -Fq 'dflash-console-updates.php' '$RemoteTheme/functions.php' || (printf '$includeBase64' | base64 -d >> '$RemoteTheme/functions.php')"
& ssh -i $key -p $port -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
    "$userName@$hostName" $remoteCommand
if ($LASTEXITCODE -ne 0) { throw "Could not enable the DFlash WordPress route." }
Write-Host "DFlash update endpoint deployed." -ForegroundColor Green
