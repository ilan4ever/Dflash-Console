param(
    [int]$Port = 0,
    [switch]$Foreground,
    [switch]$Restart,
    [switch]$ApiRestart,
    # OneVoice run.ps1: only start when the API is fully down; never kill a healthy Console.
    [switch]$DelegatedStart
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

function Test-ConsoleTrustedPort {
    param([int]$TargetPort)
    if ($TargetPort -eq $script:ConsoleUiPort) { return $true }
    if ($script:ConsoleGatewayPort -gt 0 -and $TargetPort -eq $script:ConsoleGatewayPort) { return $true }
    return $false
}

function Wait-PortFree {
    param(
        [int]$TargetPort,
        [double]$TimeoutSeconds = 15.0
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)) {
            return $true
        }
        Start-Sleep -Milliseconds 200
    }
    return -not (Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)
}

function Stop-ProcessTree {
    param([int]$ProcId)
    if ($ProcId -le 0) { return }
    & taskkill.exe /F /T /PID $ProcId 2>$null | Out-Null
}

function Stop-ListenersOnPort {
    param(
        [int]$TargetPort,
        [object[]]$Connections = $null,
        [switch]$SkipWait
    )
    $freed = @()
    if ($null -eq $Connections) {
        $Connections = @(Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)
    }
    foreach ($conn in @($Connections | Where-Object { [int]$_.LocalPort -eq $TargetPort })) {
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
                '(?i)uvicorn\s+api\.app:app',
                '(?i)-m\s+uvicorn\s+api\.app:app'
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
            # WMI often returns an empty CommandLine for python listeners started
            # from a hidden pwsh worker. On Console UI/gateway ports, treat those
            # python processes as managed so ApiRestart can actually free 8900.
            if (
                -not $managed -and
                (Test-ConsoleTrustedPort -TargetPort $TargetPort) -and
                $name -match '^(python|pythonw)(\.exe)?$'
            ) {
                $managed = $true
            }
            if (-not $managed) {
                $freed += "skipped $name ($procId)"
                continue
            }
            Stop-ProcessTree -ProcId $procId
            if (-not $SkipWait) {
                [void](Wait-PortFree -TargetPort $TargetPort -TimeoutSeconds 8)
            }
            $freed += "$name ($procId)"
        } catch {
            # already gone
        }
    }
    return $freed
}

function Get-ConsoleApiHealth {
    param([int]$TargetPort)
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$TargetPort/api/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($response.StatusCode -ne 200) { return $null }
        $payload = $response.Content | ConvertFrom-Json -ErrorAction Stop
        if (
            $payload.success -eq $true -and
            [string]$payload.app -eq 'DFlash Console' -and
            -not [string]::IsNullOrWhiteSpace([string]$payload.boot_id)
        ) {
            return $payload
        }
    } catch {
        # no healthy console on the port
    }
    return $null
}

function Test-ConsoleApiHealthy {
    param([int]$TargetPort)
    return $null -ne (Get-ConsoleApiHealth -TargetPort $TargetPort)
}

function Stop-ForeignConsoleApi {
    <#
    Stop another DFlash Console instance that currently holds the port, so only
    one Console server ever runs at a time on this machine. Tries a graceful
    /api/shutdown first (releases engines + gateway), then force-stops whatever
    still listens on the port.
    #>
    param([int]$TargetPort)
    return (Stop-ConsoleApiOnPort -TargetPort $TargetPort)
}

function Stop-ConsoleApiOnPort {
    <#
    Gracefully stop the Console API on a port, wait until the listener is gone,
    then force-stop any stale python listener that WMI cannot identify.
    #>
    param([int]$TargetPort)
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$TargetPort/api/shutdown" -Method Post -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # best-effort graceful shutdown; fall through to force stop
    }
    if (Wait-PortFree -TargetPort $TargetPort -TimeoutSeconds 20) {
        return $true
    }
    $stopped = Stop-StaleConsoleApi -TargetPort $TargetPort
    if ($stopped.Count -gt 0) {
        Write-StartupLine ("  Force-stopped Console API listener on port {0} - {1}" -f $TargetPort, ($stopped -join ', ')) 'DarkYellow'
    }
    return (Wait-PortFree -TargetPort $TargetPort -TimeoutSeconds 10)
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
$script:ConsoleUiPort = [int]$Port
$script:ConsoleGatewayPort = if ($cfg -and $cfg.gateway_port) { [int]$cfg.gateway_port } else { 8001 }

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
# Extra fallback: the checkout that owns the configured models_root. This PC's
# dev install keeps the engine + models together; a separate data root (the
# installed app) reuses them via models_root without shipping a llama.cpp copy.
$modelsRootBinary = ''
if ($cfg -and $cfg.models_root) {
    $engineRoot = Split-Path -Parent ([string]$cfg.models_root)
    $candidate = Join-Path $engineRoot 'llama.cpp\build\bin\Release\llama-server.exe'
    if (Test-Path $candidate -PathType Leaf) { $modelsRootBinary = $candidate }
}
if ($hasEnabledServer -and -not (Test-Path $llamaBinary -PathType Leaf) -and (-not $fallbackBinary -or -not (Test-Path $fallbackBinary -PathType Leaf)) -and (-not $modelsRootBinary)) {
    Write-StartupLine "ERROR: llama-server binary not found under $env:DFLASH_ROOT, ONEVOICE_ROOT, or the models root." 'Red'
    exit 1
}

$url = "http://127.0.0.1:$Port/"
$orchestratedStart = $DelegatedStart -or ([string]$env:ONEVOICE_ORCHESTRATED_CONSOLE_START -eq '1')

if ($DelegatedStart -or $orchestratedStart) {
    $delegatedExisting = Get-ConsoleApiHealth -TargetPort $Port
    if ($delegatedExisting) {
        Write-Host ''
        Write-Host "DFlash Console already running at $url" -ForegroundColor Green
        Write-StartupLine "Delegated start: Console already healthy (no restart)" 'Green'
        exit 0
    }
}

if (-not $Restart -and -not $ApiRestart -and -not $DelegatedStart) {
    $existing = Get-ConsoleApiHealth -TargetPort $Port
    if ($existing) {
        $existingRoot = [string]$existing.console_root
        $myRoot = if ($env:DFLASH_ROOT) { [IO.Path]::GetFullPath($env:DFLASH_ROOT) } else { '' }
        $sameInstance = $false
        if ($existingRoot -and $myRoot) {
            $sameInstance = [string]::Equals(
                [IO.Path]::GetFullPath($existingRoot),
                $myRoot,
                [StringComparison]::OrdinalIgnoreCase
            )
        }
        if ($sameInstance) {
            Write-Host ''
            Write-Host "DFlash Console already running at $url" -ForegroundColor Green
            Write-StartupLine "Already running at $url (no restart requested)" 'Green'
            exit 0
        }
        Write-Host ''
        Write-Host "Another DFlash Console instance ($existingRoot) holds port $Port — stopping it so only one server runs." -ForegroundColor Yellow
        Write-StartupLine "Stopping foreign Console instance on port $Port (root: $existingRoot)..." 'Yellow'
        $stopped = Stop-ForeignConsoleApi -TargetPort $Port
        if ($stopped) {
            Write-StartupLine "  Foreign Console instance stopped; taking over port $Port." 'Green'
        } else {
            Write-StartupLine "  Could not stop the other instance on port $Port." 'Red'
        }
    }
}

Write-Host ''
Write-Host '=== DFlash Console startup ===' -ForegroundColor Cyan
Write-StartupLine '=== DFlash Console startup ===' 'Cyan'
Write-StartupLine "Root: $Root"
Write-StartupLine "DFlash root: $env:DFLASH_ROOT"
Write-StartupLine "Console UI port: $Port"
Write-StartupLine ("Mode: {0}" -f $(if ($Restart) { 'full restart' } elseif ($ApiRestart) { 'API restart; preserve engines' } else { 'start if needed' })) 'Gray'

if ($Restart) {
    Write-StartupLine 'Full restart — stopping managed engines...' 'Yellow'
    # A full restart force-stops each managed listener below. Calling the
    # graceful Python unload helper first made startup wait up to 20 seconds
    # when an engine was busy, without changing the final process state.

    $ports = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ports.Add($Port)
    if ($cfg -and $cfg.servers) {
        foreach ($server in $cfg.servers) {
            if ($server.port) { [void]$ports.Add([int]$server.port) }
        }
    }

    # Query Windows networking once. Get-NetTCPConnection is comparatively
    # expensive when called once for every configured engine port.
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $ports.Contains([int]$_.LocalPort) })
    foreach ($targetPort in ($ports | Sort-Object)) {
        if ($targetPort -eq $Port) { continue }
        $portListeners = @($listeners | Where-Object { [int]$_.LocalPort -eq $targetPort })
        $stopped = Stop-ListenersOnPort -TargetPort $targetPort -Connections $portListeners -SkipWait
        if ($stopped.Count -gt 0) {
            Write-StartupLine "  Freed port $targetPort - $($stopped -join ', ')" 'DarkYellow'
        } else {
            Write-StartupLine "  Port $targetPort already free" 'DarkGray'
        }
    }
    Start-Sleep -Milliseconds 750

} else {
    Write-StartupLine 'Gentle start — preserving running llama-server engines' 'Gray'
}

if (-not $orchestratedStart) {
    if ($ApiRestart -or $Restart) {
        $released = Stop-ConsoleApiOnPort -TargetPort $Port
        if ($released) {
            Write-StartupLine '  Console API port ready for launch' 'DarkGray'
        } else {
            Write-StartupLine "  WARNING: port $Port is still in use after stop attempt" 'Red'
        }
    } else {
        $stale = Stop-StaleConsoleApi -TargetPort $Port
        if ($stale.Count -gt 0) {
            Write-StartupLine "  Stopped stale Console API — $($stale -join ', ')" 'DarkYellow'
        } else {
            Write-StartupLine '  Console API port ready for launch' 'DarkGray'
        }
    }
    $gatewayPort = if ($cfg -and $cfg.gateway_port) { [int]$cfg.gateway_port } else { 8001 }
    if ($gatewayPort -gt 0 -and $gatewayPort -ne $Port) {
        $gwStopped = Stop-ListenersOnPort -TargetPort $gatewayPort
        if ($gwStopped.Count -gt 0) {
            Write-StartupLine "  Stopped stale OpenAI gateway — $($gwStopped -join ', ')" 'DarkYellow'
        }
    }
} elseif (-not (Test-ConsoleApiHealthy -TargetPort $Port)) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-StartupLine 'Orchestrated start — waiting for existing Console API to become healthy...' 'Gray'
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if (Test-ConsoleApiHealthy -TargetPort $Port) {
                Write-StartupLine '  Existing Console API became healthy (no restart)' 'Green'
                exit 0
            }
            Start-Sleep -Seconds 1
        }
    }
    $stale = Stop-StaleConsoleApi -TargetPort $Port
    if ($stale.Count -gt 0) {
        Write-StartupLine ('  Orchestrated start - cleared stale listener: {0}' -f ($stale -join ', ')) 'DarkYellow'
    }
}

if ($cfg -and $cfg.servers) {
    Write-StartupLine 'Configured model servers:' 'Gray'
    foreach ($server in $cfg.servers) {
        $enabled = if ($null -eq $server.enabled) { $true } else { [bool]$server.enabled }
        if (-not $enabled) { continue }
        Write-StartupLine ('  - {0} - http://127.0.0.1:{1}/v1  [{2}]' -f $server.label, $server.port, $server.id) 'Gray'
    }
}

function Resolve-PwshPath {
    if ($env:PWSH_PATH -and (Test-Path -LiteralPath $env:PWSH_PATH)) {
        return $env:PWSH_PATH
    }
    $candidates = @(
        (Join-Path ${env:ProgramFiles} 'PowerShell\7\pwsh.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'PowerShell\7\pwsh.exe'),
        (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $cmd = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cmd = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-StartupLine 'ERROR: Python not found on PATH' 'Red'
    exit 1
}
$pwshPath = Resolve-PwshPath
if (-not $Foreground -and -not $pwshPath) {
    Write-StartupLine 'ERROR: PowerShell not found (install PowerShell 7 or use Windows PowerShell)' 'Red'
    exit 1
}

$requirementsLock = Join-Path $Root 'requirements.lock'
$requirementsStamp = Join-Path $logDir '.requirements.lock.sha256'
if (-not (Test-Path $requirementsLock)) {
    Write-StartupLine ('ERROR: dependency lock file not found: {0}' -f $requirementsLock) 'Red'
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
    Write-StartupLine ('Starting Console in foreground at {0}' -f $url) 'Green'
    Write-StartupLine 'Press Ctrl+C to stop.' 'Gray'
    Write-Host ''
    $env:PYTHONPATH = $Root
    & python -m uvicorn api.app:app --host 127.0.0.1 --port $Port
    exit $LASTEXITCODE
}

Write-StartupLine 'Starting Console API in background...' 'Green'
$errLog = Join-Path $logDir 'console-server.err.log'
$proc = Start-Process -FilePath $pwshPath `
    -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$worker`" -Port $Port -Root `"$Root`"" `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $serverLog `
    -RedirectStandardError $errLog

Start-Sleep -Seconds 2
Write-StartupLine ('Console API PID {0} - log: {1}' -f $proc.Id, $serverLog) 'Green'
if (-not (Wait-ConsoleApiHealthy -TargetPort $Port -Process $proc)) {
    $proc.Refresh()
    if ($proc.HasExited) {
        Write-StartupLine ('ERROR: Console API exited before becoming ready (exit code {0}). See {1}' -f $proc.ExitCode, $errLog) 'Red'
    } else {
        Write-StartupLine ('ERROR: Console API did not become healthy within 30 seconds. See {0} and {1}' -f $serverLog, $errLog) 'Red'
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    exit 1
}
if (-not $orchestratedStart) {
    Write-StartupLine ('Open UI: {0}' -f $url) 'Green'
    Write-Host ''
    Write-Host ('DFlash Console is running at {0}' -f $url) -ForegroundColor Green
    Write-Host ('Logs: {0}' -f $StartupLog) -ForegroundColor DarkGray
    Write-Host 'Use .\server.ps1 -Restart for a full engine reset.' -ForegroundColor DarkGray
    Write-Host 'Use .\server.ps1 -Foreground to attach this terminal.' -ForegroundColor DarkGray
    Write-Host ''
} else {
    Write-StartupLine ("Console API ready at {0} (orchestrated; UI not opened)" -f $url) 'Green'
}
