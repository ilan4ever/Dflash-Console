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
$pythonCandidates = New-Object System.Collections.Generic.List[string]
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    foreach ($uvVersion in @('3.11', '3.12', '3.10')) {
        try {
            $uvPath = (& uv python find $uvVersion 2>$null | Select-Object -First 1)
            if ($uvPath -and (Test-Path -LiteralPath ([string]$uvPath).Trim())) {
                $pythonCandidates.Add(([string]$uvPath).Trim())
                break
            }
        } catch {
            continue
        }
    }
}
foreach ($candidate in @('py -3.11', 'py -3.12', 'py -3.10', 'python')) {
    $pythonCandidates.Add($candidate)
}
foreach ($candidate in $pythonCandidates) {
    try {
        $version = Invoke-Expression "$candidate -c `"import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')`"" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -match '^3\.(1[0-3])$') {
            $python = $candidate
            break
        }
    } catch {
        continue
    }
}
$reuseVenv = $false
if (Test-Path -LiteralPath $venvPy) {
    try {
        $existingVersion = & $venvPy -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $existingVersion -match '^3\.(1[0-3])$') {
            $reuseVenv = $true
        }
    } catch {
        $reuseVenv = $false
    }
}
if (-not $reuseVenv -and -not $python) {
    throw 'Python 3.10-3.13 is required for the PyTorch/Transformers runtime. Install Python 3.11 or 3.12.'
}

if ($reuseVenv) {
    Write-Host 'DFLASH_PROGRESS 10 Reusing compatible Python environment'
} else {
    Write-Host 'DFLASH_PROGRESS 10 Creating Python environment'
    if (Test-Path -LiteralPath $venv) {
        Remove-Item -LiteralPath $venv -Recurse -Force
    }
    Invoke-Expression "$python -m venv `"$venv`""
    if (-not (Test-Path -LiteralPath $venvPy)) {
        throw "Failed to create venv at $venv"
    }
}

Write-Host 'DFLASH_PROGRESS 18 Upgrading pip'
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

$torchReady = $false
if ($reuseVenv) {
    & $venvPy -c "import torch" 2>$null
    $torchReady = ($LASTEXITCODE -eq 0)
}
$torchCudaReady = $false
if ($torchReady) {
    $cudaCheck = & $venvPy -c "import torch; print(torch.cuda.is_available())" 2>$null
    $torchCudaReady = ([string]$cudaCheck).Trim() -eq 'True'
}
if (-not $torchReady) {
    if ($useCuda) {
        Write-Host 'DFLASH_PROGRESS 30 Installing PyTorch (CUDA) + Transformers stack...'
        & $venvPip install torch --index-url https://download.pytorch.org/whl/cu124 | Write-Host
    } else {
        Write-Host 'DFLASH_PROGRESS 30 Installing PyTorch (CPU) + Transformers stack...'
        & $venvPip install torch --index-url https://download.pytorch.org/whl/cpu | Write-Host
    }
} elseif ($useCuda -and -not $torchCudaReady) {
    Write-Host 'DFLASH_PROGRESS 30 Restoring the CUDA PyTorch build'
    & $venvPip install --force-reinstall --no-deps torch --index-url https://download.pytorch.org/whl/cu124 | Write-Host
}

Write-Host 'DFLASH_PROGRESS 70 Installing Transformers libraries'
$requirementsPath = Join-Path $bundleDest 'requirements.txt'
$requirementsNoTorch = Join-Path $bundleDest 'requirements-no-torch.txt'
Get-Content -LiteralPath $requirementsPath |
    Where-Object { $_ -notmatch '^\s*torch(\s|$|\[)' } |
    Set-Content -LiteralPath $requirementsNoTorch -Encoding utf8
& $venvPip install --upgrade --no-input --timeout 120 --retries 1 -r $requirementsNoTorch | Write-Host

if ($useCuda) {
    $cudaAfter = & $venvPy -c "import torch; print(torch.cuda.is_available())" 2>$null
    if (([string]$cudaAfter).Trim() -ne 'True') {
        Write-Host 'DFLASH_PROGRESS 82 Restoring the CUDA PyTorch build'
        & $venvPip install --force-reinstall --no-deps torch --index-url https://download.pytorch.org/whl/cu124 | Write-Host
    }
}

$packageVersion = (& $venvPy -c "import transformers; print(transformers.__version__)" 2>$null | Select-Object -Last 1)
$manifest = @{
    version = 1
    bundle_revision = 1
    runtime_id = 'transformers'
    worker = (Join-Path $bundleDest 'server.py')
    python = $venvPy
    torch_variant = $(if ($useCuda) { 'cuda' } else { 'cpu' })
    package_version = ([string]$packageVersion).Trim()
    execution_mode = 'server'
    generated_by = 'scripts/install-transformers-runtime.ps1'
} | ConvertTo-Json -Depth 4
Set-Content -LiteralPath (Join-Path $bundleDest 'manifest.json') -Value $manifest -Encoding utf8

Write-Host "Transformers runtime installed at $bundleDest"
