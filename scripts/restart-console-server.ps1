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

$command = Get-Command pwsh.exe -ErrorAction SilentlyContinue
$shell = if ($command) { $command.Source } else { $null }
if (-not $shell) {
    $command = Get-Command powershell.exe -ErrorAction SilentlyContinue
    $shell = if ($command) { $command.Source } else { $null }
}
if (-not $shell) {
    throw 'PowerShell is required to restart the Console server.'
}

& $shell -NoProfile -ExecutionPolicy Bypass -File $serverScript @args
exit $LASTEXITCODE
