# DFlash Console — 7z SFX installer with native setup UI (Speak-style).
param(
    [switch]$SkipStage
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$SevenZip = 'C:\Program Files\7-Zip\7z.exe'
$DistDir = Join-Path $Root 'dist-electron'
$Unpacked = Join-Path $DistDir 'win-unpacked'

if (-not (Test-Path $SevenZip)) {
    throw '7-Zip is required at C:\Program Files\7-Zip\7z.exe'
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
# The branded setup UI is the installer executable itself. Shipping a bare 7z
# SFX wrapper forced the whole package to unpack silently before any window
# could appear, which left the screen empty for many seconds. Instead the UI
# carries the compressed package appended after its own bytes (see
# SetupForm.TryReadPayloadTrailer): the window is visible from the first
# moment and performs the extraction with its own progress screen.
$SetupStage = Join-Path $env:TEMP "dflash-setup-stage-$Version"
if (Test-Path $SetupStage) { Remove-Item -LiteralPath $SetupStage -Recurse -Force }
$AppStage = Join-Path $SetupStage 'app'
New-Item -ItemType Directory -Path $AppStage -Force | Out-Null
Copy-Item -Path (Join-Path $Unpacked '*') -Destination $AppStage -Recurse -Force
# Kept inside the package so older update helpers can still find and run it.
Copy-Item $setupUiExe (Join-Path $SetupStage 'setup.exe') -Force
Set-Content -Path (Join-Path $SetupStage 'install-version.txt') -Value $Version -Encoding ASCII -NoNewline

Write-Host '[3/4] Creating 7z archive...' -ForegroundColor Yellow
$TempArchive = Join-Path $env:TEMP "dflash-console-$Version.7z"
if (Test-Path $TempArchive) { Remove-Item $TempArchive -Force }
& $SevenZip a -t7z -mx5 -m0=BCJ -m1=LZMA2 -mmt=on -ssc -y $TempArchive "$SetupStage\*" | Out-Null
if ($LASTEXITCODE -ne 0) { throw '7z archive creation failed' }
Remove-Item -LiteralPath $SetupStage -Recurse -Force -ErrorAction SilentlyContinue

Write-Host '[4/4] Assembling self-contained installer...' -ForegroundColor Yellow
if (Test-Path $OutputExe) { Remove-Item $OutputExe -Force }
Copy-Item -LiteralPath $setupUiExe -Destination $OutputExe -Force
$payloadOffset = (Get-Item -LiteralPath $OutputExe).Length
$payloadLength = (Get-Item -LiteralPath $TempArchive).Length
$outputStream = [IO.File]::Open($OutputExe, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $payloadStream = [IO.File]::OpenRead($TempArchive)
    try { $payloadStream.CopyTo($outputStream) } finally { $payloadStream.Dispose() }
    $trailer = [Text.Encoding]::ASCII.GetBytes('DFLASHPL') +
        [BitConverter]::GetBytes([int64]$payloadOffset) +
        [BitConverter]::GetBytes([int64]$payloadLength)
    $outputStream.Write($trailer, 0, $trailer.Length)
} finally {
    $outputStream.Dispose()
}
Remove-Item -LiteralPath $TempArchive -Force -ErrorAction SilentlyContinue
$finalLength = (Get-Item -LiteralPath $OutputExe).Length
if (-not (Test-Path $OutputExe) -or $finalLength -ne ($payloadOffset + $payloadLength + 24)) {
    throw "Installer assembly failed (size $finalLength)"
}

Write-Host "Built $OutputExe" -ForegroundColor Green
Write-Output $OutputExe
