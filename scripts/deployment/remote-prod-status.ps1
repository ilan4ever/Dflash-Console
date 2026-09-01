$ErrorActionPreference = 'Continue'
Write-Host '=== Host ==='
hostname
whoami
Write-Host '=== Sessions ==='
quser 2>$null
Write-Host '=== Port 8900 ==='
netstat -ano | findstr ':8900'
Write-Host '=== DFlash processes ==='
Get-CimInstance Win32_Process -Filter "Name='DFlash Console.exe' OR Name='dflash-setup-ui.exe' OR Name LIKE 'DFlash-Console-Setup%'" |
    ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
        [PSCustomObject]@{ PID=$_.ProcessId; Name=$_.Name; User="$($owner.Domain)\$($owner.User)"; Cmd=$_.CommandLine }
    } | Format-Table -AutoSize
Write-Host '=== afars install ==='
$afarsInstall = 'C:\Users\afars\AppData\Local\Programs\DFlash Console'
if (Test-Path $afarsInstall) {
    Get-ChildItem $afarsInstall | Select-Object Name, Length, LastWriteTime
    if (Test-Path (Join-Path $afarsInstall 'install-version.txt')) {
        Write-Host 'version:' (Get-Content (Join-Path $afarsInstall 'install-version.txt') -Raw)
    }
} else {
    Write-Host 'afars install folder missing'
}
Write-Host '=== afars data root ==='
$afarsData = 'C:\Users\afars\DFlash Console'
if (Test-Path $afarsData) {
    Get-ChildItem $afarsData | Select-Object Name, LastWriteTime | Select-Object -First 15
} else {
    Write-Host 'afars data root missing'
}
Write-Host '=== Users matching afars ==='
Get-LocalUser | Where-Object { $_.Name -like '*afar*' -or $_.Name -like '*afars*' } | Format-Table Name, Enabled, LastLogon -AutoSize
Write-Host '=== Health ==='
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/health' -TimeoutSec 3
    Write-Host $r.Content
} catch {
    Write-Host 'health unreachable:' $_.Exception.Message
}
