$ErrorActionPreference = 'Continue'
# Fix stale install-version marker and leave API running.
$verFile = 'C:\Users\afars\AppData\Local\Programs\DFlash Console\install-version.txt'
Set-Content -Path $verFile -Value '0.3.106' -Encoding ascii -NoNewline
Write-Host 'install-version now:' (Get-Content $verFile -Raw)
Write-Host 'health:' (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/health' -TimeoutSec 5).Content
