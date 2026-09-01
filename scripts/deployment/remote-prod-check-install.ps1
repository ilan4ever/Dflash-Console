$ErrorActionPreference = 'Continue'
Write-Host '=== install log ==='
if (Test-Path 'C:\Users\developer\dflash-106-install.log') {
    Get-Content 'C:\Users\developer\dflash-106-install.log'
}
Write-Host '=== processes ==='
Get-Process | Where-Object { $_.Name -match 'DFlash|dflash|7z|setup' } | Select-Object Id, ProcessName, StartTime
Write-Host '=== afars version ==='
$vf = 'C:\Users\afars\AppData\Local\Programs\DFlash Console\install-version.txt'
if (Test-Path $vf) { Get-Content $vf -Raw }
Write-Host '=== port 8900 ==='
netstat -ano | findstr ':8900'
