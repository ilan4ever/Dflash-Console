# Install the vLLM runtime under runtimes/vllm/.
# Official vLLM is Linux-first. On Windows we try a native pip wheel first,
# then install into WSL so new machines can still get a working engine.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [ValidateSet('auto', 'native', 'wsl')]
    [string]$Backend = 'auto'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'wsl-path.ps1')
. (Join-Path $PSScriptRoot 'json-file.ps1')
$bundleDest = Join-Path $Root 'runtimes\vllm'
$venv = Join-Path $bundleDest 'venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$manifestPath = Join-Path $bundleDest 'manifest.json'
$logDir = Join-Path $Root 'logs\runtimes'
New-Item -ItemType Directory -Path $bundleDest -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Write-VllmManifest {
    param(
        [string]$InstallBackend,
        [string]$PythonPath = '',
        [string]$WslDistro = '',
        [string]$WslPython = ''
    )
    $manifest = @{
        version = 1
        bundle_revision = 1
        runtime_id = 'vllm'
        execution_mode = 'server'
        backend = $InstallBackend
        python = $PythonPath
        wsl_distro = $WslDistro
        wsl_python = $WslPython
        generated_by = 'scripts/install-vllm-runtime.ps1'
    } | ConvertTo-Json -Depth 4
    Write-Utf8JsonFile -Path $manifestPath -Content $manifest
}

function Test-NativeVllm {
    if (-not (Test-Path -LiteralPath $venvPy)) { return $false }
    & $venvPy -c "import vllm" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Install-NativeVllm {
    Write-Host 'DFLASH_PROGRESS 8 Finding Python'
    $python = $null
    foreach ($candidate in @('py -3.12', 'py -3.11', 'py -3.10', 'python')) {
        try {
            $version = Invoke-Expression "$candidate -c `"import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')`"" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version) {
                $python = $candidate
                break
            }
        } catch {
            continue
        }
    }
    if (-not $python) {
        throw 'Python 3.10+ is required to install vLLM on Windows.'
    }
    Write-Host 'DFLASH_PROGRESS 12 Creating Python environment'
    if (Test-Path -LiteralPath $venv) {
        Remove-Item -LiteralPath $venv -Recurse -Force
    }
    Invoke-Expression "$python -m venv `"$venv`""
    if (-not (Test-Path -LiteralPath $venvPy)) {
        throw "Failed to create venv at $venv"
    }
    Write-Host 'DFLASH_PROGRESS 18 Upgrading pip'
    & $venvPy -m pip install --upgrade pip wheel setuptools | Write-Host
    # Official vLLM does not ship Windows wheels. Only-binary fails in seconds
    # instead of sitting on a source build that looks frozen at 5%.
    Write-Host 'DFLASH_PROGRESS 22 Checking for a Windows vLLM wheel'
    & $venvPy -m pip install --only-binary=:all: vllm | Write-Host
    if (-not (Test-NativeVllm)) {
        throw 'No official Windows wheel for vLLM. Switching to WSL.'
    }
    Write-VllmManifest -InstallBackend 'native' -PythonPath $venvPy
    Write-Host 'DFLASH_PROGRESS 95 vLLM runtime installed natively'
    Write-Host "vLLM runtime installed natively at $bundleDest"
}

function Get-WslDistro {
    $wsl = Get-Command wsl -ErrorAction SilentlyContinue
    if (-not $wsl) { return $null }
    $names = @()
    try {
        $raw = & wsl -l -q 2>$null
        foreach ($line in @($raw)) {
            $name = ([string]$line).Replace("`0", '').Trim()
            if ($name) { $names += $name }
        }
    } catch {
        return $null
    }
    if (-not $names.Count) { return $null }
    foreach ($prefer in @('Ubuntu', 'Ubuntu-24.04', 'Ubuntu-22.04', 'Debian')) {
        $hit = $names | Where-Object { $_ -eq $prefer -or $_ -like "$prefer*" } | Select-Object -First 1
        if ($hit) { return $hit }
    }
    return $names[0]
}

function Test-WslVllm {
    param([string]$Distro, [string]$WslPython)
    if (-not $Distro -or -not $WslPython) { return $false }
    & wsl -d $Distro -- $WslPython -c "import vllm" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Install-WslVllm {
    $distro = Get-WslDistro
    if (-not $distro) {
        throw 'WSL is not ready. Install Ubuntu from Microsoft Store or run "wsl --install -d Ubuntu", restart, then click Install vLLM again. An NVIDIA GPU is required.'
    }
    Write-Host "DFLASH_PROGRESS 40 Installing vLLM inside WSL distro: $distro"
    $sh = Join-Path $PSScriptRoot 'install-vllm-wsl.sh'
    if (-not (Test-Path -LiteralPath $sh)) {
        throw 'WSL vLLM install script is missing (scripts/install-vllm-wsl.sh).'
    }
    $wslSh = Convert-WindowsPathForWsl -Path $sh
    if (-not $wslSh) {
        throw 'Could not map the WSL path for the vLLM install script.'
    }
    $output = New-Object System.Collections.Generic.List[string]
    $result = Invoke-WslBashScript -Distro $distro -WslScriptPath $wslSh
    foreach ($line in @($result.Output)) {
        [void]$output.Add([string]$line)
    }
    if ($result.ExitCode -ne 0) {
        throw "WSL vLLM install failed. An NVIDIA GPU and a working Ubuntu/WSL Python 3 install are required.`n$($output -join "`n")"
    }
    $wslPython = (@($output) | Where-Object { $_ -match 'vllm-venv/bin/python' } | Select-Object -Last 1)
    if (-not $wslPython) {
        $wslPython = '~/.dflash-console/vllm-venv/bin/python'
    }
    if (-not (Test-WslVllm -Distro $distro -WslPython $wslPython.Trim())) {
        throw 'vLLM installed in WSL but import still failed. Check NVIDIA drivers and WSL GPU passthrough.'
    }
    Write-VllmManifest -InstallBackend 'wsl' -WslDistro $distro -WslPython $wslPython.Trim()
    Write-Host "DFLASH_PROGRESS 95 vLLM runtime installed in WSL ($distro)"
    Write-Host "vLLM runtime installed in WSL ($distro)"
}

$nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $nvidia) {
    Write-Host 'Warning: nvidia-smi not found. vLLM needs an NVIDIA GPU.'
}

$nativeOk = $false
$wslOk = $false
if ($Backend -in @('auto', 'native')) {
    try {
        Install-NativeVllm
        $nativeOk = $true
    } catch {
        Write-Host $_.Exception.Message
        if ($Backend -eq 'native') { throw }
    }
}
if (-not $nativeOk -and $Backend -in @('auto', 'wsl')) {
    Install-WslVllm
    $wslOk = $true
}
if (-not $nativeOk -and -not $wslOk) {
    throw 'Could not install vLLM. Need either a native wheel or a ready WSL Ubuntu distro.'
}
