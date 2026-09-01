$ErrorActionPreference = 'Continue'
Write-Host '=== Users dir ==='
Get-ChildItem 'C:\Users' -Directory | Select-Object Name, LastWriteTime
Write-Host '=== afars profile ==='
$afarsHome = 'C:\Users\afars'
Write-Host "exists: $(Test-Path $afarsHome)"
if (Test-Path $afarsHome) {
    (Get-Acl $afarsHome).Owner
}
Write-Host '=== WMI accounts ==='
Get-CimInstance Win32_UserAccount | Where-Object { $_.Name -match 'afar' } | Select-Object Name, Domain, Disabled, LocalAccount
Write-Host '=== afars install version ==='
$verFile = 'C:\Users\afars\AppData\Local\Programs\DFlash Console\install-version.txt'
if (Test-Path $verFile) { Get-Content $verFile -Raw }
