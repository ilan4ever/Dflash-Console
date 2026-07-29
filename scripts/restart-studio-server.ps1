param(
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$cfgPath = Join-Path $Root 'config.json'
if ($Port -le 0 -and (Test-Path $cfgPath)) {
    try {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $Port = if ($cfg.ui_port) { [int]$cfg.ui_port } else { 8900 }
    } catch {
        $Port = 8900
    }
}
if ($Port -le 0) { $Port = 8900 }

if (-not $env:DFLASH_ROOT) {
    if (Test-Path $cfgPath) {
        try {
            $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
            $env:DFLASH_ROOT = if ($cfg.dflash_root) { [string]$cfg.dflash_root } else { 'C:\dev\Dflash' }
        } catch {
            $env:DFLASH_ROOT = 'C:\dev\Dflash'
        }
    } else {
        $env:DFLASH_ROOT = 'C:\dev\Dflash'
    }
}

function Stop-StudioServer {
    param([int]$TargetPort)
    Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $procId = [int]$_.OwningProcess
        if ($procId -le 0) { return }
        try {
            $proc = Get-Process -Id $procId -ErrorAction Stop
            if (@('python', 'pwsh', 'powershell') -contains $proc.ProcessName) {
                Stop-Process -Id $procId -Force -ErrorAction Stop
            }
        } catch {}
    }
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'uvicorn\s+api\.app:app' -and $_.CommandLine -match [regex]::Escape($Root) } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

$logDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$serverLog = Join-Path $logDir 'studio-server.log'
$errLog = Join-Path $logDir 'studio-server.err.log'

Write-Host "Restarting DFlash Studio on port $Port..." -ForegroundColor Cyan
Stop-StudioServer -TargetPort $Port
Start-Sleep -Seconds 1

$worker = Join-Path $Root 'scripts\start-studio-server.ps1'
Start-Process -FilePath 'pwsh.exe' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $worker, '-Port', $Port, '-Root', $Root) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $serverLog `
    -RedirectStandardError $errLog | Out-Null

Start-Sleep -Seconds 2
Write-Host "DFlash Studio: http://127.0.0.1:$Port/" -ForegroundColor Green
