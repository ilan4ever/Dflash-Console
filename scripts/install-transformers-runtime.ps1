# Install the Transformers / PyTorch runtime under runtimes/transformers/.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [ValidateSet('auto', 'cuda', 'cpu')]
    [string]$TorchVariant = 'auto'
)

$ErrorActionPreference = 'Stop'
$bundleSrc = Join-Path $Root 'runtime-bundles\transformers'
$bundleDest = Join-Path $Root 'runtimes\transformers'
$venv = Join-Path $bundleDest 'venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$venvPip = Join-Path $venv 'Scripts\pip.exe'

if (-not (Test-Path -LiteralPath (Join-Path $bundleSrc 'server.py'))) {
    throw "Missing runtime bundle source: $bundleSrc"
}

New-Item -ItemType Directory -Path $bundleDest -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $bundleSrc 'server.py') -Destination (Join-Path $bundleDest 'server.py') -Force
Copy-Item -LiteralPath (Join-Path $bundleSrc 'requirements.txt') -Destination (Join-Path $bundleDest 'requirements.txt') -Force

$python = $null
foreach ($candidate in @('py -3.11', 'py -3.12', 'py -3.10', 'python')) {
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
    throw 'Python 3.10+ is required to install the Transformers runtime.'
}

if (Test-Path -LiteralPath $venv) {
    Remove-Item -LiteralPath $venv -Recurse -Force
}
Invoke-Expression "$python -m venv `"$venv`""
if (-not (Test-Path -LiteralPath $venvPy)) {
    throw "Failed to create venv at $venv"
}

& $venvPy -m pip install --upgrade pip wheel setuptools | Write-Host

$useCuda = $false
if ($TorchVariant -eq 'cuda') {
    $useCuda = $true
} elseif ($TorchVariant -eq 'cpu') {
    $useCuda = $false
} else {
  try {
    $nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    $useCuda = [bool]$nvidia
  } catch {
    $useCuda = $false
  }
}

if ($useCuda) {
    Write-Host 'Installing PyTorch (CUDA) + Transformers stack...'
    & $venvPip install torch --index-url https://download.pytorch.org/whl/cu124 | Write-Host
} else {
    Write-Host 'Installing PyTorch (CPU) + Transformers stack...'
    & $venvPip install torch --index-url https://download.pytorch.org/whl/cpu | Write-Host
}

& $venvPip install -r (Join-Path $bundleDest 'requirements.txt') | Write-Host

$manifest = @{
    version = 1
    runtime_id = 'transformers'
    worker = (Join-Path $bundleDest 'server.py')
    python = $venvPy
    torch_variant = $(if ($useCuda) { 'cuda' } else { 'cpu' })
    execution_mode = 'server'
    generated_by = 'scripts/install-transformers-runtime.ps1'
} | ConvertTo-Json -Depth 4
Set-Content -LiteralPath (Join-Path $bundleDest 'manifest.json') -Value $manifest -Encoding utf8

Write-Host "Transformers runtime installed at $bundleDest"
