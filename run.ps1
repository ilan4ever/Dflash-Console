param(
    [int]$Port = 0,
    [switch]$Foreground,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$serverScript = Join-Path $PSScriptRoot 'server.ps1'
if (-not (Test-Path $serverScript)) {
    Write-Host 'ERROR: server.ps1 not found next to run.ps1' -ForegroundColor Red
    exit 1
}

# `run.ps1` is the developer refresh command: release the Console and
# configured engine ports before starting a clean instance. Build the
# forwarding hashtable explicitly because assigning a switch variable does
# not add it to `$PSBoundParameters`.
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
