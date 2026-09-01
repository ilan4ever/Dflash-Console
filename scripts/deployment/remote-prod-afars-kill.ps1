$ErrorActionPreference = 'Continue'
Write-Host '=== Session / admin ==='
whoami
whoami /groups | findstr /i admin
quser 2>$null

$script = @'
$ErrorActionPreference = "Continue"
taskkill /F /IM "DFlash Console.exe" /T 2>$null
taskkill /F /IM "dflash-setup-ui.exe" /T 2>$null
Get-ChildItem $env:TEMP -Filter "DFlash-Console-Setup*.exe" -ErrorAction SilentlyContinue | ForEach-Object { taskkill /F /IM $_.Name /T 2>$null }
$line = netstat -ano | findstr "127.0.0.1:8900" | findstr LISTENING
if ($line) { $pid = ($line.Trim() -split "\s+")[-1]; if ($pid -match "^\d+$") { taskkill /F /PID $pid /T 2>$null } }
"done" | Out-File "$env:TEMP\dflash-afars-kill.done" -Encoding ascii
'@

$target = 'C:\Users\developer\afars-session-kill.ps1'
Set-Content -Path $target -Value $script -Encoding UTF8

$task = 'DFlashAfarsKill'
schtasks /Delete /TN $task /F 2>$null | Out-Null
$create = schtasks /Create /TN $task /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$target`"" /SC ONCE /ST 23:59 /RU afars /IT /F 2>&1
Write-Host $create
$run = schtasks /Run /TN $task 2>&1
Write-Host $run
Start-Sleep -Seconds 5
if (Test-Path 'C:\Users\afars\AppData\Local\Temp\dflash-afars-kill.done') {
    Write-Host 'afars-session kill completed'
    Get-Content 'C:\Users\afars\AppData\Local\Temp\dflash-afars-kill.done'
} else {
    Write-Host 'afars-session kill may not have run (needs afars logged in + admin rights)'
}
schtasks /Delete /TN $task /F 2>$null | Out-Null

Write-Host '=== afars DFlash processes after task ==='
Get-CimInstance Win32_Process -Filter "Name='DFlash Console.exe' OR Name='dflash-setup-ui.exe'" |
    ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
        [PSCustomObject]@{ PID=$_.ProcessId; User="$($owner.Domain)\$($owner.User)"; Cmd=$_.CommandLine }
    } | Format-Table -AutoSize
