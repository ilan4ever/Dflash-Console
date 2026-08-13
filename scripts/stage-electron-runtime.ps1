# Stage Console backend/UI files for the Electron installer bundle.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$dest = Join-Path $Root 'electron\runtime-staging'
if (Test-Path $dest) {
    Remove-Item -LiteralPath $dest -Recurse -Force
}
New-Item -ItemType Directory -Path $dest | Out-Null

$items = @(
    'api',
    'core',
    'static',
    'assets',
    'scripts',
    'server.ps1',
    'run.ps1',
    'requirements.txt',
    'requirements.lock',
    'config.example.json',
    'LICENSE',
    'NOTICE.md',
    'TRADEMARKS.md',
    'docs\LICENSING.md'
)

foreach ($item in $items) {
    $source = Join-Path $Root $item
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing runtime item: $source"
    }
    $target = Join-Path $dest $item
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    if ((Get-Item -LiteralPath $source).PSIsContainer) {
        Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
    } else {
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
}

foreach ($sub in @('models', 'logs', 'logs\presets')) {
    New-Item -ItemType Directory -Path (Join-Path $dest $sub) -Force | Out-Null
}

$package = Get-Content -LiteralPath (Join-Path $Root 'package.json') -Raw | ConvertFrom-Json
$version = [string]$package.version
if (-not $version) {
    throw 'package.json is missing a version for the Electron runtime bundle.'
}
Set-Content -LiteralPath (Join-Path $dest '.runtime-version') -Value $version -NoNewline -Encoding ascii

Write-Host "Staged Electron runtime v$version at $dest"
