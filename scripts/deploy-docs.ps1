# Deploy DFlash Console documentation to the OneVoice website over SSH.
#
# Publishes docs/ (and optionally README.md) to:
#   ~/domains/onevoiceai.in/public_html/dflash-console/docs
# which is served at https://onevoiceai.in/dflash-console/docs/
#
# SSH target resolution:
#   1. Explicit Hostinger settings from .env.admin / environment
#      (HOSTINGER_SSH_HOST, HOSTINGER_SSH_USERNAME, HOSTINGER_SSH_PORT,
#      HOSTINGER_SSH_PRIVATE_KEY_PATH)
#   2. Otherwise the ssh config alias `neworldshop-hostinger`.
#
# Usage:
#   .\scripts\deploy-docs.ps1              # docs/ only
#   .\scripts\deploy-docs.ps1 -IncludeReadme   # docs/ + README.md

[CmdletBinding()]
param(
    [switch]$IncludeReadme
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$docsSource = Join-Path $root "docs"
$envFile = Join-Path $root ".env.admin"

function Read-EnvValue([string]$Name) {
    $environmentValue = [Environment]::GetEnvironmentVariable($Name)
    if ($environmentValue) { return $environmentValue }
    if (-not (Test-Path $envFile)) { return "" }
    $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return (($line -split "=", 2)[1]).Trim().Trim("'").Trim('"')
}

$remoteRel = "dflash-console/docs"
$hostName = Read-EnvValue "HOSTINGER_SSH_HOST"
$userName = Read-EnvValue "HOSTINGER_SSH_USERNAME"
$port = Read-EnvValue "HOSTINGER_SSH_PORT"
$key = [Environment]::ExpandEnvironmentVariables((Read-EnvValue "HOSTINGER_SSH_PRIVATE_KEY_PATH"))

if ($hostName -and $userName -and $port -and $key) {
    $remoteTarget = "$userName@$hostName"
    $baseArgs = @("-i", $key, "-p", $port, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")
    $scpArgs = @("-i", $key, "-P", $port, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")
    Write-Host "Using explicit Hostinger SSH settings." -ForegroundColor Cyan
} else {
    $remoteTarget = "neworldshop-hostinger"
    $baseArgs = @("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")
    $scpArgs = @("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")
    Write-Host "Using ssh config alias 'neworldshop-hostinger'." -ForegroundColor Cyan
}

# Resolve the absolute doc root on the server (works with both resolution paths).
# Single-quote the command so bash expands $HOME, not PowerShell.
$remoteHome = (ssh @baseArgs $remoteTarget 'echo $HOME').Trim()
if (-not $remoteHome) { throw "Could not resolve remote home." }
$remoteBase = "$remoteHome/domains/onevoiceai.in/public_html"
$remoteDir = "$remoteBase/$remoteRel"

Write-Host "Creating remote directory $remoteDir"
& ssh @baseArgs $remoteTarget "mkdir -p '$remoteDir'"
if ($LASTEXITCODE -ne 0) { throw "Could not create remote docs directory." }

Write-Host "Uploading docs/ → $remoteDir"
& scp @scpArgs -r "$docsSource/." "${remoteTarget}:$remoteDir/"
if ($LASTEXITCODE -ne 0) { throw "Could not upload docs/." }

if ($IncludeReadme) {
    Write-Host "Uploading README.md → dflash-console/"
    & scp @scpArgs (Join-Path $root "README.md") "${remoteTarget}:$remoteBase/dflash-console/README.md"
    if ($LASTEXITCODE -ne 0) { throw "Could not upload README.md." }
}

Write-Host "Docs deployed → https://onevoiceai.in/$remoteRel/" -ForegroundColor Green
