[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$Manifest,
    [string]$Token = "",
    [string]$RemoteRoot = "/home/u840646150/domains/onevoiceai.in/dflash-console-private"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $root ".env.admin"

function Read-EnvValue([string]$Name) {
    $environmentValue = [Environment]::GetEnvironmentVariable($Name)
    if ($environmentValue) { return $environmentValue }
    if (-not (Test-Path $envFile)) { return "" }
    $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return (($line -split "=", 2)[1]).Trim().Trim("'").Trim('"')
}

# The feed token can come from -Token, the DFLASH_UPDATE_TOKEN environment
# variable, or DFLASH_UPDATE_TOKEN in .env.admin (kept out of git).
if (-not $Token) {
    $Token = Read-EnvValue "DFLASH_UPDATE_TOKEN"
}
if (-not $Token) {
    Write-Warning "No update-feed token provided (-Token / DFLASH_UPDATE_TOKEN). The feed will be published without token protection."
}

$sshHost = Read-EnvValue "HOSTINGER_SSH_HOST"
$sshUser = Read-EnvValue "HOSTINGER_SSH_USERNAME"
$sshPort = Read-EnvValue "HOSTINGER_SSH_PORT"
$keyPath = Read-EnvValue "HOSTINGER_SSH_PRIVATE_KEY_PATH"
if (-not $sshHost -or -not $sshUser -or -not $sshPort -or -not $keyPath) {
    throw "Missing HOSTINGER_SSH_HOST, HOSTINGER_SSH_USERNAME, HOSTINGER_SSH_PORT, or HOSTINGER_SSH_PRIVATE_KEY_PATH in .env.admin."
}
$keyPath = [Environment]::ExpandEnvironmentVariables($keyPath)
$installerPath = (Resolve-Path $Installer).Path
$manifestPath = (Resolve-Path $Manifest).Path
$manifestJson = Get-Content $manifestPath -Raw | ConvertFrom-Json
$remoteInstaller = "$RemoteRoot/$($manifestJson.fileName)"

& ssh -i $keyPath -p $sshPort -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
    "$sshUser@$sshHost" "mkdir -p '$RemoteRoot' && chmod 700 '$RemoteRoot'"
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the private DFlash release directory." }

$remoteInstallerTemp = "$remoteInstaller.part"
$remoteManifestTemp = "$RemoteRoot/latest.json.part"
& scp -i $keyPath -P $sshPort -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
    $installerPath "${sshUser}@${sshHost}:$remoteInstallerTemp"
if ($LASTEXITCODE -ne 0) { throw "Installer upload failed." }
& scp -i $keyPath -P $sshPort -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
    $manifestPath "${sshUser}@${sshHost}:$remoteManifestTemp"
if ($LASTEXITCODE -ne 0) { throw "Manifest upload failed." }

if ($Token) {
    $tokenFile = Join-Path ([IO.Path]::GetTempPath()) "dflash-update-token-$([Guid]::NewGuid().ToString('N')).txt"
    try {
        Set-Content -LiteralPath $tokenFile -Value $Token -NoNewline -Encoding ascii
        & scp -i $keyPath -P $sshPort -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
            $tokenFile "${sshUser}@${sshHost}:$RemoteRoot/.token.part"
        if ($LASTEXITCODE -ne 0) { throw "Token upload failed." }
    } finally {
        Remove-Item $tokenFile -Force -ErrorAction SilentlyContinue
    }
}

$commit = "mv '$remoteInstallerTemp' '$remoteInstaller' && mv '$remoteManifestTemp' '$RemoteRoot/latest.json'"
if ($Token) { $commit += " && mv '$RemoteRoot/.token.part' '$RemoteRoot/.token' && chmod 600 '$RemoteRoot/.token'" }
$commit += " && chmod 600 '$remoteInstaller' '$RemoteRoot/latest.json'"
& ssh -i $keyPath -p $sshPort -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
    "$sshUser@$sshHost" $commit
if ($LASTEXITCODE -ne 0) { throw "Could not publish the DFlash release atomically." }
Write-Host "Published DFlash Console $($manifestJson.version) to $RemoteRoot" -ForegroundColor Green
