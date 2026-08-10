param(
    [int]$Port = 0,
    [switch]$Foreground,
    [switch]$Restart,
    [switch]$NoElectron
)

$ErrorActionPreference = 'Stop'
$PSScriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$serverScript = Join-Path $PSScriptRoot 'server.ps1'
$electronScript = Join-Path $PSScriptRoot 'scripts\run-electron.ps1'

if (-not (Test-Path $serverScript)) {
    Write-Host 'ERROR: server.ps1 not found next to run.ps1' -ForegroundColor Red
    exit 1
}

# `run.ps1` is the developer launch command. By default it opens the developer
# Electron app (the desktop window) — the Electron shell starts the Console
# server itself and connects to it, so the window shows the Developer badge.
# Pass -NoElectron to start the server only (browser UI at http://127.0.0.1:<port>/).

if ($NoElectron) {
    # Legacy server-only mode: release the Console and configured engine
    # ports before starting a clean instance.
    $forwardParams = @{
        Restart = $true
    }
    if ($Port -gt 0) {
        $forwardParams.Port = $Port
    }
    if ($Foreground) {
        $forwardParams.Foreground = $true
    }
    & $serverScript @forwardParams
    exit $LASTEXITCODE
}

if (-not (Test-Path $electronScript)) {
    Write-Host 'ERROR: scripts\run-electron.ps1 not found; cannot open the Electron app.' -ForegroundColor Red
    Write-Host 'Use .\run.ps1 -NoElectron to start the server only (browser UI).' -ForegroundColor Yellow
    exit 1
}

$targetPort = if ($Port -gt 0) { $Port } else { 8900 }

if ($Restart) {
    Write-Host 'Restarting the Console server first...' -ForegroundColor Cyan
    # Detached so server.ps1's own `exit` does not close this launcher.
    Start-Process -FilePath 'pwsh' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $serverScript, '-Port', "$targetPort", '-Restart') `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden | Out-Null
    $deadline = (Get-Date).AddSeconds(180)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$targetPort/api/health" -TimeoutSec 2
            if ($health.success) { $ready = $true; break }
        } catch {
            # not up yet — keep polling
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Write-Host 'WARNING: Console API did not report healthy in time; opening Electron anyway (it will retry).' -ForegroundColor Yellow
    }
}

Write-Host 'Starting the developer Electron app...' -ForegroundColor Cyan
& $electronScript
exit $LASTEXITCODE
