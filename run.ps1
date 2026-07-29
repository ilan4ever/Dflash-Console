param(
    [int]$Port = 0,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
Set-Location $Root

function Write-StartupLine {
    param([string]$Message, [string]$Color = 'Gray')
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$stamp] $Message"
    Write-Host $line -ForegroundColor $Color
    Add-Content -Path $script:StartupLog -Value $line
}

function Stop-ListenersOnPort {
    param([int]$TargetPort)
    $freed = @()
    $connections = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $connections) {
        $procId = [int]$conn.OwningProcess
        if ($procId -le 0) { continue }
        try {
            $proc = Get-Process -Id $procId -ErrorAction Stop
            $name = $proc.ProcessName
            $allowed = @('python', 'llama-server', 'pwsh', 'powershell')
            if ($allowed -notcontains $name) {
                $freed += "skipped $name ($procId)"
                continue
            }
            Stop-Process -Id $procId -Force -ErrorAction Stop
            $freed += "$name ($procId)"
        } catch {
            # already gone
        }
    }
    return $freed
}

$logDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$StartupLog = Join-Path $logDir 'startup.log'
"" | Set-Content -Path $StartupLog
$serverLog = Join-Path $logDir 'studio-server.log'

$cfgPath = Join-Path $Root 'config.json'
$cfg = $null
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    } catch {
        Write-StartupLine "Warning: could not parse config.json - using defaults." 'Yellow'
    }
}

if ($Port -le 0) {
    $Port = if ($cfg -and $cfg.ui_port) { [int]$cfg.ui_port } else { 8900 }
}

if (-not $env:DFLASH_ROOT) {
    $env:DFLASH_ROOT = if ($cfg -and $cfg.dflash_root) { [string]$cfg.dflash_root } else { 'C:\dev\Dflash' }
}

$ports = [System.Collections.Generic.HashSet[int]]::new()
[void]$ports.Add($Port)
if ($cfg -and $cfg.servers) {
    foreach ($server in $cfg.servers) {
        if ($server.port) { [void]$ports.Add([int]$server.port) }
    }
}

Write-Host ''
Write-Host '=== DFlash Studio startup ===' -ForegroundColor Cyan
Write-StartupLine '=== DFlash Studio startup ===' 'Cyan'
Write-StartupLine "Root: $Root"
Write-StartupLine "DFlash root: $env:DFLASH_ROOT"
Write-StartupLine "Studio UI port: $Port"

Write-StartupLine 'Stopping previous Studio / llama-server listeners...' 'Yellow'
foreach ($targetPort in ($ports | Sort-Object)) {
    $stopped = Stop-ListenersOnPort -TargetPort $targetPort
    if ($stopped.Count -gt 0) {
        Write-StartupLine "  Freed port $targetPort - $($stopped -join ', ')" 'DarkYellow'
    } else {
        Write-StartupLine "  Port $targetPort already free" 'DarkGray'
    }
}

Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    Write-StartupLine "  Stopped llama-server (PID $($_.Id))" 'DarkYellow'
}

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'uvicorn\s+api\.app:app' -and $_.CommandLine -match [regex]::Escape($Root) } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-StartupLine "  Stopped stale Studio python (PID $($_.ProcessId))" 'DarkYellow'
    }

if ($cfg -and $cfg.servers) {
    Write-StartupLine 'Configured model servers (start from UI after Studio loads):' 'Gray'
    foreach ($server in $cfg.servers) {
        $enabled = if ($null -eq $server.enabled) { $true } else { [bool]$server.enabled }
        if (-not $enabled) { continue }
        Write-StartupLine "  - $($server.label) - http://127.0.0.1:$($server.port)/v1  [$($server.id)]" 'Gray'
    }
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-StartupLine 'ERROR: Python not found on PATH' 'Red'
    exit 1
}

Write-StartupLine 'Installing Python dependencies...' 'Gray'
& python -m pip install -q -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-StartupLine 'ERROR: pip install failed' 'Red'
    exit 1
}

$worker = Join-Path $Root 'scripts\start-studio-server.ps1'
$url = "http://127.0.0.1:$Port/"

if ($Foreground) {
    Write-StartupLine "Starting Studio in foreground at $url" 'Green'
    Write-StartupLine "Press Ctrl+C to stop." 'Gray'
    Write-Host ''
    $env:PYTHONPATH = $Root
    & python -m uvicorn api.app:app --host 127.0.0.1 --port $Port
    exit $LASTEXITCODE
}

Write-StartupLine 'Starting Studio API in background...' 'Green'
$errLog = Join-Path $logDir 'studio-server.err.log'
$proc = Start-Process -FilePath 'pwsh.exe' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $worker, '-Port', $Port, '-Root', $Root) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $serverLog `
    -RedirectStandardError $errLog

Start-Sleep -Seconds 2
Write-StartupLine "Studio API PID $($proc.Id) - log: $serverLog" 'Green'
Write-StartupLine "Open UI: $url" 'Green'
Write-Host ''
Write-Host "DFlash Studio is running at $url" -ForegroundColor Green
Write-Host "Logs: $StartupLog" -ForegroundColor DarkGray
Write-Host "Use .\run.ps1 -Foreground to attach this terminal." -ForegroundColor DarkGray
Write-Host ''
