# DFlash Console — 7z SFX installer with native setup UI (Speak-style).
param(
    [switch]$SkipStage
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$SevenZip = 'C:\Program Files\7-Zip\7z.exe'
$SfxStub = Join-Path $Root 'tools\7zS2.sfx'
$DistDir = Join-Path $Root 'dist-electron'
$Unpacked = Join-Path $DistDir 'win-unpacked'

if (-not (Test-Path $SevenZip)) {
    throw '7-Zip is required at C:\Program Files\7-Zip\7z.exe'
}
if (-not (Test-Path $SfxStub)) {
    throw "Missing SFX stub: $SfxStub"
}

$pkg = Get-Content (Join-Path $Root 'package.json') -Raw | ConvertFrom-Json
$Version = [string]$pkg.version
$ExeName = 'DFlash Console.exe'
$ArtifactName = "DFlash-Console-Setup-$Version-x64.exe"
$OutputExe = Join-Path $DistDir $ArtifactName

if (-not $SkipStage) {
    & (Join-Path $PSScriptRoot 'stage-electron-runtime.ps1') -Root $Root
}

Write-Host "=== DFlash Console Fast Installer ===" -ForegroundColor Cyan
Write-Host "Version : $Version"

if (Test-Path $Unpacked) {
    Remove-Item -LiteralPath $Unpacked -Recurse -Force
}

Write-Host '[1/4] Packaging unpacked app...' -ForegroundColor Yellow
& npm run dist:dir
if ($LASTEXITCODE -ne 0) { throw 'electron-builder --dir failed' }

$mainExe = Join-Path $Unpacked $ExeName
if (-not (Test-Path $mainExe)) {
    throw "Packaging failed: $ExeName not found in $Unpacked"
}

Write-Host '[2/4] Building native setup UI...' -ForegroundColor Yellow
$setupUiScript = Join-Path $Root 'tools\dflash-setup-ui\build.ps1'
$setupUiExe = Join-Path $Root 'tools\dflash-setup-ui\bin\dflash-setup-ui.exe'
& $setupUiScript | Out-Host
if (-not (Test-Path $setupUiExe)) {
    throw 'dflash-setup-ui.exe was not built'
}
# 7zS2.sfx ignores ;!@Install@! config. It extracts to %TEMP%\7z* and then
# launches the first match of: start / run / install / setup / *.exe.
# If DFlash Console.exe sits at archive root, Windows "installs" from Temp.
$SfxStage = Join-Path $env:TEMP "dflash-sfx-stage-$Version"
if (Test-Path $SfxStage) { Remove-Item -LiteralPath $SfxStage -Recurse -Force }
$AppStage = Join-Path $SfxStage 'app'
New-Item -ItemType Directory -Path $AppStage -Force | Out-Null
Copy-Item -Path (Join-Path $Unpacked '*') -Destination $AppStage -Recurse -Force
Copy-Item $setupUiExe (Join-Path $SfxStage 'setup.exe') -Force
Set-Content -Path (Join-Path $SfxStage 'install-version.txt') -Value $Version -Encoding ASCII -NoNewline

Write-Host '[3/4] Creating 7z archive...' -ForegroundColor Yellow
$TempArchive = Join-Path $env:TEMP "dflash-console-$Version.7z"
if (Test-Path $TempArchive) { Remove-Item $TempArchive -Force }
& $SevenZip a -t7z -mx5 -m0=BCJ -m1=LZMA2 -mmt=on -ssc -y $TempArchive "$SfxStage\*" | Out-Null
if ($LASTEXITCODE -ne 0) { throw '7z archive creation failed' }
Remove-Item -LiteralPath $SfxStage -Recurse -Force -ErrorAction SilentlyContinue

Write-Host '[4/4] Building SFX installer...' -ForegroundColor Yellow
# Do not prepend a config block. 7zS2 expects stub + 7z archive only.
if (Test-Path $OutputExe) { Remove-Item $OutputExe -Force }
cmd /c copy /b "$SfxStub" + "$TempArchive" "$OutputExe" | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $OutputExe)) {
    throw 'SFX assembly failed'
}

Write-Host "Built $OutputExe" -ForegroundColor Green
Write-Output $OutputExe
