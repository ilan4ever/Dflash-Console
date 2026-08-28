param(
    [switch]$Test,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$OutDir = Join-Path $Root 'dist-pypi'
$token = $env:PYPI_TOKEN
if (-not $token) { $token = $env:TWINE_PASSWORD }
if (-not $token) {
    Write-Error 'Set PYPI_TOKEN or TWINE_PASSWORD to a PyPI API token, then rerun.'
}

if (-not $SkipBuild) {
    if (Test-Path $OutDir) {
        Remove-Item $OutDir -Recurse -Force
    }
    python -m pip install --quiet --upgrade build twine
    python -m build --outdir $OutDir
}

$repo = if ($Test) { 'https://test.pypi.org/legacy/' } else { 'https://upload.pypi.org/legacy/' }
$env:TWINE_USERNAME = '__token__'
$env:TWINE_PASSWORD = $token
python -m twine upload --repository-url $repo (Join-Path $OutDir '*')
Write-Host 'Published dflash-console. Install with: pip install dflash-console'
