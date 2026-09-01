$ErrorActionPreference = 'Continue'
Write-Host '=== health ==='
try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/health' -TimeoutSec 5).Content } catch { $_.Exception.Message }
Write-Host '=== PID 13308 ==='
Get-CimInstance Win32_Process -Filter 'ProcessId=13308' | Select-Object Name, CommandLine
Write-Host '=== DFlash processes detail ==='
Get-CimInstance Win32_Process -Filter "Name='DFlash Console.exe' OR Name='dflash-setup-ui.exe'" | ForEach-Object {
    $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
    [PSCustomObject]@{ PID=$_.ProcessId; User="$($o.Domain)\$($o.User)"; Cmd=$_.CommandLine }
} | Format-List
Write-Host '=== install-version + exe time ==='
$root = 'C:\Users\afars\AppData\Local\Programs\DFlash Console'
Get-Item (Join-Path $root 'install-version.txt'), (Join-Path $root 'DFlash Console.exe') -ErrorAction SilentlyContinue | Select-Object FullName, Length, LastWriteTime
Write-Host '=== setup logs in temp ==='
Get-ChildItem $env:TEMP, 'C:\Users\afars\AppData\Local\Temp' -Filter '*dflash*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object FullName, Length, LastWriteTime -First 15
Write-Host '=== powershell install still running? ==='
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*remote-prod-install-106*' } | Select-Object ProcessId, CommandLine
