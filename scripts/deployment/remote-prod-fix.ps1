$ErrorActionPreference = 'Continue'
Write-Host '=== DFlash production recovery ===' -ForegroundColor Cyan

$names = @('DFlash Console', 'dflash-setup-ui', 'DFlash-Console-Setup', '7zFM', '7zG')
foreach ($name in $names) {
    Get-Process -Name $name -ErrorAction SilentlyContinue |
        Select-Object Id, ProcessName, MainWindowTitle, Responding
}

Write-Host '--- port 8900 ---'
netstat -ano | findstr ':8900' | findstr LISTENING

Write-Host '--- killing DFlash / setup UI ---'
foreach ($name in $names) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "Stopping $($_.ProcessName) PID $($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}
taskkill /F /IM "DFlash Console.exe" /T 2>$null | Out-Host
taskkill /F /IM "dflash-setup-ui.exe" /T 2>$null | Out-Host

$line = netstat -ano | findstr '127.0.0.1:8900' | findstr LISTENING
if ($line) {
    $portPid = ($line.Trim() -split '\s+')[-1]
    if ($portPid -match '^\d+$') {
        Write-Host "Stopping listener on 8900 PID $portPid"
        taskkill /F /PID $portPid /T 2>$null | Out-Host
    }
}

Start-Sleep -Seconds 2
Write-Host '--- after cleanup ---'
Get-Process -Name 'DFlash Console','dflash-setup-ui' -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, MainWindowTitle

$installDir = Join-Path $env:LOCALAPPDATA 'Programs\DFlash Console'
$setup = Get-ChildItem -Path (Join-Path $env:USERPROFILE 'Downloads'), $env:TEMP, 'C:\dev\Dflash-Console\dist-electron' -Filter 'DFlash-Console-Setup-0.3.106-x64.exe' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $setup) {
    $setup = Get-ChildItem -Path 'C:\Users\afars\Downloads','C:\Users\developer\Downloads' -Filter 'DFlash-Console-Setup*.exe' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}
if ($setup) {
    Write-Host "Found installer: $($setup.FullName)"
} else {
    Write-Host 'No local installer found yet.'
}
if (Test-Path $installDir) {
    Write-Host "Installed app: $installDir"
    Get-Content (Join-Path $installDir 'install-version.txt') -ErrorAction SilentlyContinue
}
