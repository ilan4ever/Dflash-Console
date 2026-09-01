$ErrorActionPreference = 'Continue'
$log = 'C:\Users\afars\AppData\Local\Temp\dflash-setup-ui.log'
if (Test-Path $log) { Get-Content $log } else { Write-Host 'no setup log' }
Write-Host '--- install-version bytes ---'
$p = 'C:\Users\afars\AppData\Local\Programs\DFlash Console\install-version.txt'
if (Test-Path $p) {
    $bytes = [System.IO.File]::ReadAllBytes($p)
    Write-Host ('content: ' + [System.Text.Encoding]::ASCII.GetString($bytes))
    Write-Host ('length: ' + $bytes.Length)
}
Write-Host '--- package version in asar path ---'
$pkg = 'C:\Users\afars\AppData\Local\Programs\DFlash Console\resources\app.asar'
if (Test-Path $pkg) { Write-Host "asar exists, size $((Get-Item $pkg).Length)" }
