# Install the optional FreeToken runtime inside a WSL2 Linux distribution.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Distro = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'wsl-path.ps1')
. (Join-Path $PSScriptRoot 'json-file.ps1')
$bundle = Join-Path $Root 'runtimes\freetoken'
$manifestPath = Join-Path $bundle 'manifest.json'
$logDir = Join-Path $Root 'logs\runtimes'
$scriptPath = Join-Path $PSScriptRoot 'install-freetoken-wsl.sh'

New-Item -ItemType Directory -Path $bundle -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Get-WslDistro {
    param([string]$Requested)
    if ($Requested) { return $Requested }
    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) { return $null }
    $names = @(& wsl -l -q 2>$null | ForEach-Object {
        ([string]$_).Replace("`0", '').Trim()
    } | Where-Object { $_ })
    foreach ($preferred in @('Ubuntu', 'Ubuntu-24.04', 'Ubuntu-22.04', 'Debian')) {
        $match = $names | Where-Object { $_ -eq $preferred -or $_ -like "$preferred*" } | Select-Object -First 1
        if ($match) { return $match }
    }
    return ($names | Select-Object -First 1)
}

if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
    throw 'WSL is not installed. Run "wsl --install -d Ubuntu", restart Windows, then try again.'
}
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw 'FreeToken WSL install script is missing.'
}

$selectedDistro = Get-WslDistro -Requested $Distro
if (-not $selectedDistro) {
    throw 'No WSL Linux distribution was found. Install Ubuntu with "wsl --install -d Ubuntu", then try again.'
}

Write-Host "DFLASH_PROGRESS 8 Preparing WSL distro: $selectedDistro"
$wslScript = Convert-WindowsPathForWsl -Path $scriptPath

$output = New-Object System.Collections.Generic.List[string]
$result = Invoke-WslBashScript -Distro $selectedDistro -WslScriptPath $wslScript
foreach ($line in @($result.Output)) {
    [void]$output.Add([string]$line)
}
if ($result.ExitCode -ne 0) {
    throw "FreeToken WSL installation failed.`n$($output -join "`n")"
}

$pythonLine = @($output) | Where-Object { $_ -match '^FREETOKEN_WSL_PYTHON=' } | Select-Object -Last 1
if (-not $pythonLine) {
    throw 'FreeToken installed but the WSL Python path was not reported.'
}
$wslPython = $pythonLine -replace '^FREETOKEN_WSL_PYTHON=', ''
if (-not $wslPython) {
    throw 'FreeToken installed but its WSL Python path is empty.'
}
$ftLine = @($output) | Where-Object { $_ -match '^FREETOKEN_WSL_FT=' } | Select-Object -Last 1
$wslFt = if ($ftLine) { $ftLine -replace '^FREETOKEN_WSL_FT=', '' } else { "$([System.IO.Path]::GetDirectoryName($wslPython))/ft" }

Write-Host 'DFLASH_PROGRESS 96 Verifying FreeToken runtime'
$prevPref = $ErrorActionPreference
$prevNative = $false
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $prevNative = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
}
$ErrorActionPreference = 'Continue'
try {
    & wsl -d $selectedDistro -- $wslFt --version 2>&1 | ForEach-Object { Write-Host ([string]$_) }
    $ftExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevPref
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
        $PSNativeCommandUseErrorActionPreference = $prevNative
    }
}
if ($ftExit -ne 0) {    throw 'FreeToken was installed, but its ft command check failed. Verify the NVIDIA driver and WSL CUDA passthrough.'
}

$manifest = @{
    version = 1
    bundle_revision = 1
    runtime_id = 'freetoken'
    execution_mode = 'server'
    backend = 'wsl'
    wsl_distro = $selectedDistro
    wsl_python = $wslPython
    wsl_ft = $wslFt
    generated_by = 'scripts/install-freetoken-runtime.ps1'
} | ConvertTo-Json -Depth 4
Write-Utf8JsonFile -Path $manifestPath -Content $manifest

Write-Host 'DFLASH_PROGRESS 100 FreeToken WSL runtime installed'
