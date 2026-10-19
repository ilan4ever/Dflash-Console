param(
    [int]$Port = 8900,
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'
if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}
Set-Location $Root

if (-not $env:DFLASH_ROOT) {
    $env:DFLASH_ROOT = $Root
}
$env:PYTHONPATH = $Root

$py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
& $py -m uvicorn api.app:app --host 127.0.0.1 --port $Port --loop core.win_loop:loop_factory
