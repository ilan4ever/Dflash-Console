param(
    [int]$Port = 0,
    [switch]$Foreground,
    [switch]$Restart,
    [switch]$NoElectron
)

$ErrorActionPreference = 'Stop'
$PSScriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }

if ($env:ONEVOICE_ORCHESTRATED_CONSOLE_START -eq '1') {
    Write-Host 'Skipping DFlash Console run.ps1 — OneVoice is orchestrating Console startup (no UI, no restart).' -ForegroundColor Yellow
    exit 0
}

$serverScript = Join-Path $PSScriptRoot 'server.ps1'
$electronScript = Join-Path $PSScriptRoot 'scripts\run-electron.ps1'

if (-not (Test-Path $serverScript)) {
    Write-Host 'ERROR: server.ps1 not found next to run.ps1' -ForegroundColor Red
    exit 1
}

$targetPort = if ($Port -gt 0) { $Port } else { 8900 }

# Close any running DFlash Console desktop app (the developer Electron for this
# repo and the installed app) — a full process stop, not just hiding the window.
function Stop-DflashApps {
    Write-Host 'Closing the running DFlash Console app...' -ForegroundColor Cyan
    Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'electron.exe' -and $_.CommandLine -and $_.CommandLine -match 'Dflash-Console' -and $_.CommandLine -notmatch '--type=') -or
        ($_.Name -eq 'DFlash Console.exe' -and $_.CommandLine -and $_.CommandLine -notmatch '--type=')
    } | ForEach-Object {
        Write-Host "  closing PID $($_.ProcessId) ($($_.Name))" -ForegroundColor DarkGray
        & taskkill.exe /F /T /PID $_.ProcessId 2>$null | Out-Null
    }
    Start-Sleep -Seconds 2
}

# Stop whatever Console server is listening on the port: graceful /api/shutdown
# first, then force-kill the listener quickly. The graceful endpoint may be
# releasing a busy engine, but a full restart also clears engine listeners below.
function Stop-ConsoleServer {
    param([int]$Port)
    Write-Host "Stopping the Console server on port $Port..." -ForegroundColor Cyan
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/shutdown" -Method Post -TimeoutSec 5 | Out-Null
    } catch {
        # not running or already gone
    }
    $deadline = (Get-Date).AddSeconds(3)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 300
    }
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Host "  force-stopping listener PID $($listener.OwningProcess)" -ForegroundColor DarkGray
        & taskkill.exe /F /T /PID $listener.OwningProcess 2>$null | Out-Null
        Start-Sleep -Seconds 2
    }
}

# Start the Console server detached and wait until it reports healthy.
function Start-ConsoleServer {
    param([int]$Port)
    Write-Host "Starting the Console server on port $Port..." -ForegroundColor Cyan
    Start-Process -FilePath 'pwsh' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $serverScript, '-Port', "$Port", '-Restart') `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden | Out-Null
    $deadline = (Get-Date).AddSeconds(180)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
            if ($health.success) { $ready = $true; break }
        } catch {
            # not up yet — keep polling
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Write-Host 'WARNING: Console API did not report healthy in time; opening the app anyway (it will retry).' -ForegroundColor Yellow
    }
}

# `run.ps1` is the developer launch command. EVERY run is a full restart: it
# closes the running app, stops the server, starts a fresh server, then opens
# the developer Electron app (detached, so this terminal is freed immediately).
# Pass -NoElectron to start the server only (browser UI at http://127.0.0.1:<port>/).

if ($NoElectron) {
    # Server-only mode: close the app, then restart the server.
    Stop-DflashApps
    Stop-ConsoleServer -Port $targetPort
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

# Full restart every time: close the app, stop the server, start it fresh, then
# open the Electron app (detached so the terminal returns immediately).
Stop-DflashApps
Stop-ConsoleServer -Port $targetPort
Start-ConsoleServer -Port $targetPort

Write-Host 'Starting the developer Electron app...' -ForegroundColor Cyan
Start-Process -FilePath 'pwsh' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $electronScript) `
    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden | Out-Null
exit 0
