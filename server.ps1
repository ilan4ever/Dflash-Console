param(
    [int]$Port = 0,
    [switch]$Foreground,
    [switch]$Restart,
    [switch]$ApiRestart
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
Set-Location $Root

function Rotate-LogFile {
    param(
        [string]$Path,
        [int64]$MaxBytes = 5242880,
        [int]$Backups = 3
    )
    if (-not (Test-Path $Path)) { return }
    if ((Get-Item $Path).Length -lt $MaxBytes) { return }
    try {
        for ($index = $Backups - 1; $index -ge 1; $index--) {
            $source = "$Path.$index"
            $destination = "$Path.$($index + 1)"
            if (Test-Path $source) {
                Move-Item -Force $source $destination -ErrorAction Stop
            }
        }
        Move-Item -Force $Path "$Path.1" -ErrorAction Stop
    } catch {
        # Another process may hold the log open (Electron shell / watcher). Skip rotate.
    }
}

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
            $details = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction Stop
            $commandLine = [string]$details.CommandLine
            $identity = "$name $commandLine"
            # Built-in managed-process regexes (raw patterns, Console-owned).
            $ManagedPatterns = @(
                '(?i)llama-server',
                '(?i)start_llama_server\.ps1',
                '(?i)uvicorn\s+api\.app:app'
            )
            # Registered runtime tokens (e.g. Piper) written by the Console to
            # runtimes\process-tokens.json at boot. These are literal substrings,
            # so escape them before matching.
            $TokensFile = Join-Path $Root 'runtimes\process-tokens.json'
            if (Test-Path $TokensFile) {
                try {
                    $manifestTokens = @((Get-Content $TokensFile -Raw | ConvertFrom-Json).tokens)
                    foreach ($token in $manifestTokens) {
                        $ManagedPatterns += "(?i)$([regex]::Escape([string]$token))"
                    }
                } catch {
                    # manifest is optional; keep the built-in token set
                }
            }
            $managed = $false
            foreach ($pattern in $ManagedPatterns) {
                if ($identity -match $pattern) {
                    $managed = $true
                    break
                }
            }
            if (-not $managed) {
                $freed += "skipped $name ($procId)"
                continue
            }
            Stop-Process -Id $procId -Force -ErrorAction Stop
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
                if (-not (Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)) {
                    break
                }
                Start-Sleep -Milliseconds 100
            }
            $freed += "$name ($procId)"
        } catch {
            # already gone
        }
    }
    return $freed
}

function Test-ConsoleApiHealthy {
    param([int]$TargetPort)
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$TargetPort/api/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($response.StatusCode -ne 200) { return $false }
        $payload = $response.Content | ConvertFrom-Json -ErrorAction Stop
        return (
            $payload.success -eq $true -and
            [string]$payload.app -eq 'DFlash Console' -and
            -not [string]::IsNullOrWhiteSpace([string]$payload.boot_id)
        )
    } catch {
        return $false
    }
}

function Wait-ConsoleApiHealthy {
    param(
        [int]$TargetPort,
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 30
    )
    for ($attempt = 0; $attempt -lt $TimeoutSeconds; $attempt++) {
        if ($Process) {
            $Process.Refresh()
            if ($Process.HasExited) { return $false }
        }
        if (Test-ConsoleApiHealthy -TargetPort $TargetPort) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Stop-StaleConsoleApi {
    param([int]$TargetPort)
    $stopped = Stop-ListenersOnPort -TargetPort $TargetPort
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'uvicorn\s+api\.app:app' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped += "console python ($($_.ProcessId))"
        }
    return $stopped
}

$logDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$StartupLog = Join-Path $logDir 'startup.log'
Rotate-LogFile -Path $StartupLog
$serverLog = Join-Path $logDir 'console-server.log'
Rotate-LogFile -Path $serverLog
Rotate-LogFile -Path (Join-Path $logDir 'console-server.err.log')

$cfgPath = Join-Path $Root 'config.json'
$cfg = $null
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    } catch {
        Write-StartupLine 'Warning: could not parse config.json - using defaults.' 'Yellow'
    }
}

if ($Port -le 0) {
    $Port = if ($cfg -and $cfg.ui_port) { [int]$cfg.ui_port } else { 8900 }
}

if ($cfg -and $cfg.dflash_root) {
    $env:DFLASH_ROOT = [string]$cfg.dflash_root
} elseif (-not $env:DFLASH_ROOT) {
    $env:DFLASH_ROOT = $Root
}

if (-not (Test-Path $env:DFLASH_ROOT -PathType Container)) {
    Write-StartupLine "ERROR: DFlash root does not exist: $env:DFLASH_ROOT" 'Red'
    exit 1
}
$needsRouterLauncher = $false
$hasEnabledServer = $false
if ($cfg -and $cfg.servers) {
    foreach ($server in $cfg.servers) {
        $enabled = if ($null -eq $server.enabled) { $true } else { [bool]$server.enabled }
        $embedding = ([string]$server.engine_mode -eq 'embedding') -or ([string]$server.profile -eq 'nomic-embed')
        if ($enabled) { $hasEnabledServer = $true }
        if ($enabled -and -not $embedding) { $needsRouterLauncher = $true }
    }
}
$routerLauncher = Join-Path $env:DFLASH_ROOT 'scripts\start_llama_server.ps1'
if ($needsRouterLauncher -and -not (Test-Path $routerLauncher -PathType Leaf)) {
    Write-StartupLine "ERROR: DFlash router launcher not found: $routerLauncher" 'Red'
    exit 1
}
$llamaBinary = Join-Path $env:DFLASH_ROOT 'llama.cpp\build\bin\Release\llama-server.exe'
$fallbackBinary = if ($env:ONEVOICE_ROOT) {
    Join-Path $env:ONEVOICE_ROOT '.tmp\llama-b8418-win-cuda12\llama-server.exe'
} else {
    ''
}
if ($hasEnabledServer -and -not (Test-Path $llamaBinary -PathType Leaf) -and (-not $fallbackBinary -or -not (Test-Path $fallbackBinary -PathType Leaf))) {
    Write-StartupLine "ERROR: llama-server binary not found under $env:DFLASH_ROOT or ONEVOICE_ROOT" 'Red'
    exit 1
}

$url = "http://127.0.0.1:$Port/"

if (-not $Restart -and -not $ApiRestart -and (Test-ConsoleApiHealthy -TargetPort $Port)) {
    Write-Host ''
    Write-Host "DFlash Console already running at $url" -ForegroundColor Green
    Write-StartupLine "Already running at $url (no restart requested)" 'Green'
    exit 0
}

Write-Host ''
Write-Host '=== DFlash Console startup ===' -ForegroundColor Cyan
Write-StartupLine '=== DFlash Console startup ===' 'Cyan'
Write-StartupLine "Root: $Root"
Write-StartupLine "DFlash root: $env:DFLASH_ROOT"
Write-StartupLine "Console UI port: $Port"
Write-StartupLine ("Mode: {0}" -f $(if ($Restart) { 'full restart' } elseif ($ApiRestart) { 'API restart; preserve engines' } else { 'start if needed' })) 'Gray'

if ($Restart) {
    Write-StartupLine 'Full restart — releasing managed GPU and stopping engines...' 'Yellow'

    $releaseScript = Join-Path $Root 'scripts\release-managed-gpu.py'
    if (Test-Path $releaseScript) {
        try {
            $env:PYTHONPATH = $Root
            & python $releaseScript | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "managed GPU release returned exit code $LASTEXITCODE"
            }
            Write-StartupLine '  Released managed GPU checkpoints (unload + stop engines)' 'DarkYellow'
        } catch {
            Write-StartupLine "  Managed GPU release skipped: $($_.Exception.Message)" 'DarkGray'
        }
    }

    $ports = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ports.Add($Port)
    if ($cfg -and $cfg.servers) {
        foreach ($server in $cfg.servers) {
            if ($server.port) { [void]$ports.Add([int]$server.port) }
        }
    }

    foreach ($targetPort in ($ports | Sort-Object)) {
        if ($targetPort -eq $Port) { continue }
        $stopped = Stop-ListenersOnPort -TargetPort $targetPort
        if ($stopped.Count -gt 0) {
            Write-StartupLine "  Freed port $targetPort - $($stopped -join ', ')" 'DarkYellow'
        } else {
            Write-StartupLine "  Port $targetPort already free" 'DarkGray'
        }
    }

} else {
    Write-StartupLine 'Gentle start — preserving running llama-server engines' 'Gray'
}

$stale = Stop-StaleConsoleApi -TargetPort $Port
if ($stale.Count -gt 0) {
    Write-StartupLine "  Stopped stale Console API — $($stale -join ', ')" 'DarkYellow'
} else {
    Write-StartupLine '  Console API port ready for launch' 'DarkGray'
}

if ($cfg -and $cfg.servers) {
    Write-StartupLine 'Configured model servers:' 'Gray'
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
$pwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
if (-not $pwsh) {
    Write-StartupLine 'ERROR: PowerShell 7 (pwsh.exe) not found on PATH' 'Red'
    exit 1
}

$requirementsLock = Join-Path $Root 'requirements.lock'
$requirementsStamp = Join-Path $logDir '.requirements.lock.sha256'
if (-not (Test-Path $requirementsLock)) {
    Write-StartupLine "ERROR: dependency lock file not found: $requirementsLock" 'Red'
    exit 1
}
$requirementsHash = (Get-FileHash -Algorithm SHA256 $requirementsLock).Hash
$installedHash = if (Test-Path $requirementsStamp) { (Get-Content $requirementsStamp -Raw).Trim() } else { '' }
if ($requirementsHash -ne $installedHash) {
    Write-StartupLine 'Installing pinned Python dependencies...' 'Gray'
    & python -m pip install -q -r $requirementsLock
    if ($LASTEXITCODE -ne 0) {
        Write-StartupLine 'ERROR: pip install failed' 'Red'
        exit 1
    }
    Set-Content -Path $requirementsStamp -Value $requirementsHash
} else {
    Write-StartupLine 'Pinned Python dependencies already installed' 'DarkGray'
}

$worker = Join-Path $Root 'scripts\start-console-server.ps1'

if ($Foreground) {
    Write-StartupLine "Starting Console in foreground at $url" 'Green'
    Write-StartupLine 'Press Ctrl+C to stop.' 'Gray'
    Write-Host ''
    $env:PYTHONPATH = $Root
    & python -m uvicorn api.app:app --host 127.0.0.1 --port $Port
    exit $LASTEXITCODE
}

Write-StartupLine 'Starting Console API in background...' 'Green'
$errLog = Join-Path $logDir 'console-server.err.log'
$proc = Start-Process -FilePath 'pwsh.exe' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $worker, '-Port', $Port, '-Root', $Root) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $serverLog `
    -RedirectStandardError $errLog

Start-Sleep -Seconds 2
Write-StartupLine "Console API PID $($proc.Id) - log: $serverLog" 'Green'
if (-not (Wait-ConsoleApiHealthy -TargetPort $Port -Process $proc)) {
    $proc.Refresh()
    if ($proc.HasExited) {
        Write-StartupLine "ERROR: Console API exited before becoming ready (exit code $($proc.ExitCode)). See $errLog" 'Red'
    } else {
        Write-StartupLine "ERROR: Console API did not become healthy within 30 seconds. See $serverLog and $errLog" 'Red'
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    exit 1
}
Write-StartupLine "Open UI: $url" 'Green'
Write-Host ''
Write-Host "DFlash Console is running at $url" -ForegroundColor Green
Write-Host "Logs: $StartupLog" -ForegroundColor DarkGray
Write-Host 'Use .\server.ps1 -Restart for a full engine reset.' -ForegroundColor DarkGray
Write-Host 'Use .\server.ps1 -Foreground to attach this terminal.' -ForegroundColor DarkGray
Write-Host ''
