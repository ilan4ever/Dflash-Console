param(
    [switch]$Test,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$envFile = Join-Path $Root '.env.admin'
function Read-EnvValue([string]$Name) {
    $environmentValue = [Environment]::GetEnvironmentVariable($Name)
    if ($environmentValue) { return $environmentValue }
    if (-not (Test-Path $envFile)) { return '' }
    $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
    if (-not $line) { return '' }
    return (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'")
}

$OutDir = Join-Path $Root 'dist-pypi'
$token = Read-EnvValue 'PYPI_TOKEN'
if (-not $token) { $token = $env:TWINE_PASSWORD }
if (-not $token) {
    Write-Error 'Set PYPI_TOKEN in .env.admin or TWINE_PASSWORD, then rerun.'
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
