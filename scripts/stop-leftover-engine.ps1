<#
.SYNOPSIS
    Stops a leftover DFlash Console engine process.

.DESCRIPTION
    A Console instance that was started "as administrator" spawns its
    llama-server engines detached (CREATE_BREAKAWAY_FROM_JOB) so a Console
    restart never interrupts a loaded model. Those engines inherit the elevated
    token, so a normally-launched Console cannot unload or kill them: taskkill
    returns "Access is denied" and a plain llama-server has no unload API.

    This script ends such a leftover through the WMI provider, which runs as
    SYSTEM and therefore works without an elevated prompt. It refuses to touch
    anything that is not a llama-server serving a model file from the Console
    models tree, so it cannot stop an unrelated application.

.EXAMPLE
    # Run in an elevated PowerShell (Win+X -> Terminal (Admin))
    .\scripts\stop-leftover-engine.ps1 -ProcessId 93728

.EXAMPLE
    # Find and stop every leftover engine listening on a port no longer used
    .\scripts\stop-leftover-engine.ps1 -AllLeftovers
#>
[CmdletBinding(DefaultParameterSetName = 'Pid')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Pid')]
    [int]$ProcessId,

    [Parameter(Mandatory = $true, ParameterSetName = 'Port')]
    [int]$Port,

    [Parameter(Mandatory = $true, ParameterSetName = 'All')]
    [switch]$AllLeftovers
)

$ErrorActionPreference = 'Stop'

function Test-IsElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ConsoleRoot {
    $candidates = @()
    if ($env:DFLASH_ROOT) { $candidates += $env:DFLASH_ROOT }
    if ($env:DFLASH_ROOT_OVERRIDE) { $candidates += $env:DFLASH_ROOT_OVERRIDE }
    $candidates += (Split-Path -Parent $PSScriptRoot)
    $candidates += 'C:\Users\ilanp\DFlash Console'
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return (Resolve-Path -LiteralPath $path).Path
        }
    }
    return $null
}

function Get-EngineModelPath {
    param([int]$ListenPort)
    try {
        $props = Invoke-RestMethod -Uri "http://127.0.0.1:$ListenPort/props" -TimeoutSec 3
    } catch {
        return $null
    }
    foreach ($key in 'model_path', 'model_alias') {
        $raw = [string]$props.$key
        if ($raw -match '[\\/]') { return $raw }
    }
    return $null
}

function Stop-LeftoverEngine {
    param([int]$TargetPid)

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$TargetPid" -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Host "PID $TargetPid is not running."
        return $true
    }
    if ($proc.Name -ne 'llama-server.exe') {
        Write-Host "Refusing: PID $TargetPid is '$($proc.Name)', not llama-server.exe."
        return $false
    }

    $owned = $false
    $ports = @()
    try {
        $ports = (Get-NetTCPConnection -State Listen -OwningProcess $TargetPid -ErrorAction SilentlyContinue).LocalPort
    } catch { }

    # Preferred ownership proof: the engine reports a model file inside the
    # Console tree (works even when the command line is not readable).
    $root = Get-ConsoleRoot
    foreach ($listenPort in $ports) {
        $modelPath = Get-EngineModelPath -ListenPort $listenPort
        if (-not $modelPath) { continue }
        if ($root -and ($modelPath -like "$root*")) {
            Write-Host "PID $TargetPid serves '$modelPath' on port $listenPort (Console tree)."
            $owned = $true
            break
        }
        Write-Host "PID $TargetPid serves '$modelPath' on port $listenPort (outside '$root')."
    }
    if (-not $owned) {
        # Fall back to the process executable path.
        if ($proc.ExecutablePath -and $root -and ($proc.ExecutablePath -like "$root*")) {
            Write-Host "PID $TargetPid runs '$($proc.ExecutablePath)' (Console tree)."
            $owned = $true
        }
    }
    if (-not $owned) {
        Write-Host "Refusing: cannot confirm PID $TargetPid belongs to the DFlash Console."
        return $false
    }

    # The WMI provider runs as SYSTEM, so it can end a process that inherited
    # an elevated token even when taskkill says "Access is denied".
    try {
        Get-CimInstance Win32_Process -Filter "ProcessId=$TargetPid" -ErrorAction Stop |
            Invoke-CimMethod -MethodName Terminate | Out-Null
    } catch {
        Write-Host ("WMI terminate failed for PID $TargetPid : " + $_.Exception.Message)
    }
    Start-Sleep -Milliseconds 800
    $alive = $null -ne (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)
    if ($alive) {
        Write-Host "PID $TargetPid is still running - rerun this script in an elevated PowerShell."
        return $false
    }
    Write-Host "Stopped PID $TargetPid."
    return $true
}

$targets = @()
switch ($PSCmdlet.ParameterSetName) {
    'Pid' { $targets = @($ProcessId) }
    'Port' {
        $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $conn) {
            Write-Host "Nothing is listening on port $Port."
            exit 0
        }
        $targets = @($conn | Select-Object -ExpandProperty OwningProcess -Unique)
    }
    'All' {
        $configPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'config.json'
        $allowed = @()
        if (Test-Path -LiteralPath $configPath) {
            $cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
            $allowed = @($cfg.servers | Where-Object { $_.enabled -ne $false -and $_.engine_on -eq $true } | ForEach-Object { [int]$_.port })
        }
        $targets = @(
            Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue |
                Where-Object { $allowed -notcontains 0 } |
                ForEach-Object { $_.Id }
        )
        # Keep only engines whose listening port is not a configured engine port.
        $filtered = @()
        foreach ($candidate in $targets) {
            $ports = @((Get-NetTCPConnection -State Listen -OwningProcess $candidate -ErrorAction SilentlyContinue).LocalPort)
            if ($ports.Count -eq 0) { continue }
            if (-not ($ports | Where-Object { $allowed -contains $_ })) { $filtered += $candidate }
        }
        $targets = $filtered
    }
}

if (-not $targets -or $targets.Count -eq 0) {
    Write-Output 'No leftover engines found.'
    exit 0
}

$failed = 0
foreach ($target in $targets) {
    if (-not (Stop-LeftoverEngine -TargetPid ([int]$target))) { $failed++ }
}

Write-Output ''
Write-Output 'Current GPU compute processes:'
& nvidia-smi --query-compute-apps=pid,process_name --format=csv
exit $failed
