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

if (-not (Test-Path (Join-Path $Root 'node_modules\electron'))) {
    Write-Host 'Installing Electron dependencies...' -ForegroundColor Gray
    & npm install
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Build) {
    & (Join-Path $PSScriptRoot 'build-fast-installer.ps1')
    exit $LASTEXITCODE
}

& npm run electron
exit $LASTEXITCODE
