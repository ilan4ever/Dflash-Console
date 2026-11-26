# Build the small launcher installed as <install root>\DFlash Console.exe.
# It starts the app version named in current.txt (side-by-side updates).
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
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) {
    throw "csc.exe not found (need .NET Framework 4.x)"
}

# Shortcuts and the taskbar take their icon from this exe, so it carries the
# app logo. Build a multi-size .ico (PNG entries) from the square-padded logo.
Add-Type -AssemblyName System.Drawing
$logo = Join-Path $ProjectRoot 'assets\dflash_console_logo_only_clear.png'
$icoPath = Join-Path $OutDir 'launcher.ico'
$source = [System.Drawing.Image]::FromFile($logo)
try {
    $sizes = @(16, 24, 32, 48, 64, 128, 256)
    $images = @()
    foreach ($size in $sizes) {
        $bmp = New-Object System.Drawing.Bitmap $size, $size
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $g.Clear([System.Drawing.Color]::Transparent)
        $scale = [Math]::Min($size / $source.Width, $size / $source.Height)
        $w = [int][Math]::Round($source.Width * $scale)
        $h = [int][Math]::Round($source.Height * $scale)
        $g.DrawImage($source, [int](($size - $w) / 2), [int](($size - $h) / 2), $w, $h)
        $g.Dispose()
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        $images += , @($size, $ms.ToArray())
    }
} finally {
    $source.Dispose()
}
$stream = [System.IO.File]::Create($icoPath)
$writer = New-Object System.IO.BinaryWriter $stream
try {
    $writer.Write([UInt16]0); $writer.Write([UInt16]1); $writer.Write([UInt16]$images.Count)
    $offset = 6 + 16 * $images.Count
    foreach ($entry in $images) {
        $size = $entry[0]; $bytes = $entry[1]
        $dim = if ($size -ge 256) { 0 } else { $size }
        $writer.Write([byte]$dim); $writer.Write([byte]$dim)
        $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([UInt16]1); $writer.Write([UInt16]32)
        $writer.Write([UInt32]$bytes.Length); $writer.Write([UInt32]$offset)
        $offset += $bytes.Length
    }
    foreach ($entry in $images) { $writer.Write([byte[]]$entry[1]) }
} finally {
    $writer.Dispose()
}

$out = Join-Path $OutDir "DFlash Console.exe"
$compilerArgs = @(
    '/nologo', '/target:winexe', '/optimize+', '/platform:anycpu',
    '/reference:System.Windows.Forms.dll',
    '/reference:System.dll',
    "/win32icon:$icoPath",
    "/out:$out",
    (Join-Path $PSScriptRoot 'Launcher.cs'),
    (Join-Path $ProjectRoot 'tools\dflash-setup-ui\AppLayout.cs')
)
& $csc @compilerArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $out)) {
    throw "Failed to compile the DFlash Console launcher"
}

Write-Host "Built $out ($([math]::Round((Get-Item $out).Length/1KB,1)) KB)"
Write-Output $out
