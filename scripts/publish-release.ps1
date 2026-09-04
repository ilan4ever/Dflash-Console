param(
    [ValidateSet('patch', 'minor', 'major')]
    [string]$Part = 'patch',
    [string]$SetVersion = '',
    [switch]$SkipBump,
    [switch]$SkipBuild,
    [switch]$SkipTag,
    [switch]$SkipPyPI,
    [switch]$WaitForGitHub
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Get-ProjectVersion {
    return (Get-Content (Join-Path $Root 'package.json') -Raw | ConvertFrom-Json).version
}

if (-not $SkipBump) {
    if ($SetVersion) {
        & (Join-Path $Root 'scripts\bump-version.ps1') -SetVersion $SetVersion
    } else {
        & (Join-Path $Root 'scripts\bump-version.ps1') -Part $Part
    }
}

$version = Get-ProjectVersion
Write-Host "Release version: $version"

if (-not $SkipBuild) {
    & (Join-Path $Root 'scripts\run-electron.ps1') -Build
}

if (-not $SkipTag) {
    $tag = "v$version"
    $existing = git tag -l $tag
    if (-not $existing) {
        git tag -a $tag -m "DFlash Console v$version"
    } else {
        Write-Host "Tag $tag already exists; skipping tag creation."
    }
    git push origin main
    git push origin $tag
}

if (-not $SkipPyPI) {
    & (Join-Path $Root 'scripts\publish-pypi.ps1')
}

if ($WaitForGitHub) {
    Write-Host 'Waiting for GitHub Windows release workflow...'
    Start-Sleep -Seconds 10
    $run = gh run list --workflow release-windows.yml --limit 1 --json databaseId,status,conclusion,url | ConvertFrom-Json | Select-Object -First 1
    if ($run) {
        gh run watch $run.databaseId --exit-status | Out-Host
        Write-Host "GitHub release workflow: $($run.url)"
    }
}

Write-Host ''
Write-Host "Release $version published:"
Write-Host "  GitHub tag: v$version (Windows installer via Actions)"
Write-Host "  PyPI:       pip install dflash-console==$version"
Write-Host "  Verify:     https://pypi.org/project/dflash-console/$version/"
