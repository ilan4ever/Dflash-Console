param(
    [switch]$Build,
    [switch]$DirOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) {
    Write-Host 'ERROR: npm not found on PATH. Install Node.js 20+ first.' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $Root 'node_modules\electron'))) {
    Write-Host 'Installing Electron dependencies...' -ForegroundColor Gray
    & npm install
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Build) {
    if ($DirOnly) {
        & npm run dist:dir
    } else {
        & npm run dist
    }
    exit $LASTEXITCODE
}

& npm run electron
exit $LASTEXITCODE
