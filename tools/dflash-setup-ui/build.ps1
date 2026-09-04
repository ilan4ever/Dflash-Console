# Build tiny WinForms setup UI (immediate progress — no Electron delay).
param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $OutDir = Join-Path $PSScriptRoot "bin"
}
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$cscCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) {
    throw "csc.exe not found (need .NET Framework 4.x)"
}

$pkg = Get-Content (Join-Path $ProjectRoot 'package.json') -Raw | ConvertFrom-Json
$version = [string]$pkg.version
if ($version -notmatch '^\d+\.\d+\.\d+') {
    throw "package.json version is missing or invalid: $version"
}
$versionCs = Join-Path $PSScriptRoot 'SetupVersion.cs'
@(
    'namespace DFlashConsoleSetup'
    '{'
    '    internal static class SetupVersion'
    '    {'
    "        public const string Value = `"$version`";"
    '    }'
    '}'
) | Set-Content -Path $versionCs -Encoding ASCII

$srcDir = Join-Path $ProjectRoot "tools\dflash-setup-ui"
$out = Join-Path $OutDir "dflash-setup-ui.exe"
& $csc /nologo /target:winexe /optimize+ /platform:anycpu `
    /reference:System.Windows.Forms.dll `
    /reference:System.Drawing.dll `
    /reference:System.dll `
    /out:"$out" `
    (Join-Path $srcDir "SetupForm.cs") `
    (Join-Path $srcDir "UninstallOptionsForm.cs") `
    "$versionCs"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $out)) {
    throw "Failed to compile dflash-setup-ui.exe"
}

Write-Host "Built $out ($([math]::Round((Get-Item $out).Length/1KB,1)) KB)"
Write-Output $out
