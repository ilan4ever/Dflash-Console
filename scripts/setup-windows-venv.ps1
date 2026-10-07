# First-time Windows dev setup: project .venv for server.ps1 / run.ps1.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $Root

Write-Host '=== DFlash Console — Windows venv setup ===' -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host ''

$venv = Join-Path $Root '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$requirementsLock = Join-Path $Root 'requirements.lock'

if (-not (Test-Path -LiteralPath $requirementsLock)) {
    throw "Missing dependency lock: $requirementsLock"
}

$bootstrapPython = $null
foreach ($candidate in @('py -3.14', 'py -3.13', 'py -3.12', 'py -3.11', 'py -3.10')) {
    try {
        $version = Invoke-Expression "$candidate -c `"import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')`"" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            $bootstrapPython = $candidate
            Write-Host "Bootstrap Python: $candidate ($version)"
            break
        }
    } catch {
        continue
    }
}
if (-not $bootstrapPython) {
    throw 'Python 3.10+ is required (install from python.org or use the py launcher).'
}

if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host 'Creating .venv ...'
    Invoke-Expression "$bootstrapPython -m venv `"$venv`""
}
if (-not (Test-Path -LiteralPath $venvPy)) {
    throw "Failed to create venv at $venv"
}

Write-Host 'Installing pinned server dependencies (requirements.lock) ...'
& $venvPy -m pip install -U pip wheel setuptools
& $venvPy -m pip install -r $requirementsLock

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host ''
Write-Host "  Server Python: $venvPy"
Write-Host '  Start:         .\run.ps1'
Write-Host ''
