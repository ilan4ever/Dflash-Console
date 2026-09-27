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

# Prefer the real PowerShell 7 install over the WindowsApps stub.
$pwshExe = 'C:\Program Files\PowerShell\7\pwsh.exe'
if (-not (Test-Path -LiteralPath $pwshExe)) {
    $cmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $pwshExe = $cmd.Source }
    else { $pwshExe = 'pwsh' }
}

if (-not (Test-Path -LiteralPath $serverScript)) {
    Write-Host 'ERROR: server.ps1 not found next to run.ps1' -ForegroundColor Red
    exit 1
}

$targetPort = if ($Port -gt 0) { $Port } else { 8900 }

# Close any running DFlash Console desktop app (the developer Electron for this
# repo and the installed app) — a full process stop, not just hiding the window.
function Stop-DflashApps {
    Write-Host 'Closing the running DFlash Console app...' -ForegroundColor Cyan
    $repoRoot = [regex]::Escape($PSScriptRoot)
    $deadline = (Get-Date).AddSeconds(15)
    $remaining = @()
    do {
        $targets = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $cmd = [string]$_.CommandLine
            $isMain = $cmd -notmatch '--type='
            $isDevElectron = (
                $_.Name -eq 'electron.exe' -and $isMain -and $cmd -and (
                    $cmd -match '(?i)dflash-console' -or
                    $cmd -match $repoRoot
                )
            )
            $isInstalled = (
                $_.Name -eq 'DFlash Console.exe' -and $isMain
            )
            $isDevElectron -or $isInstalled
        })
        foreach ($proc in $targets) {
            Write-Host "  closing PID $($proc.ProcessId) ($($proc.Name))" -ForegroundColor DarkGray
            & taskkill.exe /F /T /PID $proc.ProcessId 2>$null | Out-Null
        }
        Start-Sleep -Milliseconds 400
        $remaining = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $cmd = [string]$_.CommandLine
            $isMain = $cmd -notmatch '--type='
            (
                $_.Name -eq 'electron.exe' -and $isMain -and $cmd -and (
                    $cmd -match '(?i)dflash-console' -or
                    $cmd -match $repoRoot
                )
            ) -or (
                $_.Name -eq 'DFlash Console.exe' -and $isMain
            )
        })
        if ($remaining.Count -eq 0) { break }
    } while ((Get-Date) -lt $deadline)
    if ($remaining.Count -gt 0) {
        Write-Host "  warning: $($remaining.Count) desktop shell(s) still present after kill wait" -ForegroundColor Yellow
    }
}

# Stop whatever Console server is listening on the port: graceful /api/shutdown
# first, then force-kill the listener. Wait until the port is actually free.
function Stop-ConsoleServer {
    param([int]$Port)
    Write-Host "Stopping the Console server on port $Port..." -ForegroundColor Cyan
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/shutdown" -Method Post -TimeoutSec 5 | Out-Null
    } catch {
        # not running or already gone
    }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 300
    }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $procId = [int]$listener.OwningProcess
        if ($procId -le 0) { continue }
        Write-Host "  force-stopping listener PID $procId" -ForegroundColor DarkGray
        & taskkill.exe /F /T /PID $procId 2>$null | Out-Null
    }
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 300
    }
}

# Start the Console server detached and wait until it reports healthy.
function Start-ConsoleServer {
    param([int]$Port)
    Write-Host "Starting the Console server on port $Port..." -ForegroundColor Cyan
    Start-Process -FilePath $pwshExe `
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

function Ensure-ElectronExe {
    $electronExe = Join-Path $PSScriptRoot 'node_modules\electron\dist\electron.exe'
    if (Test-Path -LiteralPath $electronExe) { return $electronExe }
    Write-Host 'electron.exe missing; running npm install once...' -ForegroundColor Yellow
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        Write-Host 'ERROR: npm not found on PATH; cannot install Electron.' -ForegroundColor Red
        exit 1
    }
    Push-Location $PSScriptRoot
    try {
        & npm install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: npm install failed with exit code $LASTEXITCODE" -ForegroundColor Red
            exit 1
        }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $electronExe)) {
        Write-Host "ERROR: electron.exe still missing at $electronExe after npm install." -ForegroundColor Red
        exit 1
    }
    return $electronExe
}

function Get-MainElectronForRepo {
    param([string]$ElectronExePath)
    $escaped = [regex]::Escape($ElectronExePath)
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq 'electron.exe' -and
        [string]$_.CommandLine -and
        [string]$_.CommandLine -notmatch '--type=' -and
        [string]$_.CommandLine -match $escaped
    })
}

function Start-DeveloperElectron {
    $electronExe = Ensure-ElectronExe
    $logDir = Join-Path $PSScriptRoot 'logs'
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $launchLog = Join-Path $logDir 'electron-launch.log'
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

    Write-Host 'Starting the developer Electron app (direct electron.exe)...' -ForegroundColor Cyan
    $proc = Start-Process -FilePath $electronExe `
        -ArgumentList @('.') `
        -WorkingDirectory $PSScriptRoot `
        -PassThru
    if (-not $proc) {
        Add-Content -Path $launchLog -Value "[$stamp] FAILED to Start-Process electron.exe path=$electronExe"
        Write-Host 'ERROR: Start-Process did not return a process for electron.exe.' -ForegroundColor Red
        exit 1
    }

    Add-Content -Path $launchLog -Value "[$stamp] starting electron.exe pid=$($proc.Id) path=$electronExe Root=$PSScriptRoot"

    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        try { $proc.Refresh() } catch { }
        if ($proc.HasExited) {
            $code = $proc.ExitCode
            $stampDone = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
            Add-Content -Path $launchLog -Value "[$stampDone] electron.exe exited early code=$code pid=$($proc.Id)"
            Write-Host "ERROR: Electron exited immediately with code $code." -ForegroundColor Red
            Write-Host "See logs\electron-launch.log and any main-process error for details." -ForegroundColor Yellow
            exit 1
        }
        $alive = @(Get-MainElectronForRepo -ElectronExePath $electronExe)
        if ($alive.Count -gt 0) {
            $pidAlive = $alive[0].ProcessId
            Write-Host "Electron is running (PID $pidAlive)." -ForegroundColor Green
            return $pidAlive
        }
    }

    # PassThru still alive but WMI match lagged — treat PassThru as success.
    try { $proc.Refresh() } catch { }
    if (-not $proc.HasExited) {
        Write-Host "Electron is running (PID $($proc.Id))." -ForegroundColor Green
        return $proc.Id
    }
    $code = $proc.ExitCode
    $stampDone = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $launchLog -Value "[$stampDone] electron.exe exited code=$code pid=$($proc.Id) (after wait)"
    Write-Host "ERROR: Electron exited with code $code." -ForegroundColor Red
    Write-Host "See logs\electron-launch.log and any main-process error for details." -ForegroundColor Yellow
    exit 1
}

# `run.ps1` is the developer launch command. EVERY run is a full restart: it
# closes the running app, stops the server, starts a fresh server, then opens
# the developer Electron app directly (not via npm).
# Pass -NoElectron to start the server only (browser UI at http://127.0.0.1:<port>/).

if ($NoElectron) {
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

Stop-DflashApps
Stop-ConsoleServer -Port $targetPort
Start-ConsoleServer -Port $targetPort
$null = Start-DeveloperElectron
exit 0
