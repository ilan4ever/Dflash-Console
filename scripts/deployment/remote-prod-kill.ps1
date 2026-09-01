$ErrorActionPreference = 'Continue'
Write-Host '=== Process owners ==='
Get-CimInstance Win32_Process -Filter "Name='DFlash Console.exe'" |
    ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
        [PSCustomObject]@{
            PID = $_.ProcessId
            User = if ($owner.User) { "$($owner.Domain)\$($owner.User)" } else { '?' }
            CommandLine = $_.CommandLine
        }
    } | Format-Table -AutoSize

Write-Host '=== Force kill all DFlash ==='
cmd /c 'taskkill /F /IM "DFlash Console.exe" /T' 2>&1
cmd /c 'taskkill /F /IM "dflash-setup-ui.exe" /T' 2>&1
cmd /c 'taskkill /F /IM "DFlash-Console-Setup*.exe" /T' 2>&1

Start-Sleep -Seconds 3
$left = Get-Process -Name 'DFlash Console','dflash-setup-ui' -ErrorAction SilentlyContinue
if ($left) {
    Write-Host 'Still running:'
    $left | Format-Table Id,ProcessName -AutoSize
} else {
    Write-Host 'All DFlash/setup UI processes stopped.'
}

Write-Host '=== afars profile paths ==='
$afars = 'C:\Users\afars'
@(
    "$afars\AppData\Local\Programs\DFlash Console\install-version.txt",
    "$afars\Downloads",
    "$afars\DFlash Console\logs\startup.log"
) | ForEach-Object {
    if (Test-Path $_) { Write-Host "OK $_" } else { Write-Host "MISS $_" }
}
