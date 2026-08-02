param(
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$serverScript = Join-Path $Root 'server.ps1'
if (-not (Test-Path $serverScript)) {
    Write-Host "ERROR: server.ps1 not found at $serverScript" -ForegroundColor Red
    exit 1
}

$args = @('-ApiRestart')
if ($Port -gt 0) {
    $args += @('-Port', [string]$Port)
}

& pwsh.exe -NoProfile -ExecutionPolicy Bypass -File $serverScript @args
exit $LASTEXITCODE
