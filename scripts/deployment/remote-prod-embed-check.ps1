$log = 'C:\Users\afars\DFlash Console\logs\engine-status.log'
Select-String -Path $log -Pattern 'nomic|embed' -ErrorAction SilentlyContinue | Select-Object -Last 15 | ForEach-Object { $_.Line }
Write-Host '--- tail ---'
Get-Content $log -Tail 25 -ErrorAction SilentlyContinue
