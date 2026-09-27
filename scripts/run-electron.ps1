param(
    [switch]$Build,
    [switch]$DirOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) {
    Write-Host 'ERROR: npm not found on PATH. Install Node.js 22.12+ first.' -ForegroundColor Red
    exit 1
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host 'ERROR: node not found on PATH. Install Node.js 22.12+ first.' -ForegroundColor Red
    exit 1
}
$nodeVersionText = (& $node.Source --version).Trim().TrimStart('v')
try {
    $nodeVersion = [version]$nodeVersionText
} catch {
    Write-Host "ERROR: Could not parse Node.js version '$nodeVersionText'." -ForegroundColor Red
    exit 1
}
if ($nodeVersion -lt [version]'22.12.0') {
    Write-Host "ERROR: Node.js 22.12+ is required; found $nodeVersionText." -ForegroundColor Red
    exit 1
}

$electronExe = Join-Path $Root 'node_modules\electron\dist\electron.exe'
if (-not (Test-Path -LiteralPath $electronExe)) {
    Write-Host 'Installing Electron dependencies...' -ForegroundColor Gray
    & npm install
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not (Test-Path -LiteralPath $electronExe)) {
    Write-Host "ERROR: electron.exe missing at $electronExe" -ForegroundColor Red
    exit 1
}

if ($Build) {
    & (Join-Path $PSScriptRoot 'build-fast-installer.ps1')
    exit $LASTEXITCODE
}

$logDir = Join-Path $Root 'logs'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$launchLog = Join-Path $logDir 'electron-launch.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

# Launch Electron directly (not via npm). npm wraps the process and races with
# single-instance lock / early exit, which produced exit code 1 and no window.
Write-Host "Starting electron.exe directly: $electronExe" -ForegroundColor Cyan
$proc = Start-Process -FilePath $electronExe `
    -ArgumentList @('.') `
    -WorkingDirectory $Root `
    -PassThru
if (-not $proc) {
    Add-Content -Path $launchLog -Value "[$stamp] FAILED Start-Process electron.exe path=$electronExe"
    Write-Host 'ERROR: Start-Process failed for electron.exe' -ForegroundColor Red
    exit 1
}
Add-Content -Path $launchLog -Value "[$stamp] starting electron.exe pid=$($proc.Id) path=$electronExe Root=$Root (run-electron.ps1)"

$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    try { $proc.Refresh() } catch { }
    if ($proc.HasExited) {
        $code = $proc.ExitCode
        $stampDone = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -Path $launchLog -Value "[$stampDone] electron.exe exited early code=$code pid=$($proc.Id)"
        Write-Host "ERROR: Electron exited with code $code. See logs\electron-launch.log" -ForegroundColor Red
        exit 1
    }
    # Give the main process a moment to stay up, then report success.
    if (((Get-Date) - $proc.StartTime).TotalSeconds -ge 2) {
        Write-Host "Electron is running (PID $($proc.Id))." -ForegroundColor Green
        exit 0
    }
}

try { $proc.Refresh() } catch { }
if (-not $proc.HasExited) {
    Write-Host "Electron is running (PID $($proc.Id))." -ForegroundColor Green
    exit 0
}
$code = $proc.ExitCode
$stampDone = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Add-Content -Path $launchLog -Value "[$stampDone] electron.exe exited code=$code pid=$($proc.Id)"
Write-Host "ERROR: Electron exited with code $code. See logs\electron-launch.log" -ForegroundColor Red
exit 1
