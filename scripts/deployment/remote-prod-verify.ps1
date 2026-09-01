$ErrorActionPreference = 'Continue'
Write-Host 'VERSION:' (Get-Content 'C:\Users\afars\AppData\Local\Programs\DFlash Console\install-version.txt' -Raw).Trim()
Write-Host 'HEALTH:'
(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/health' -TimeoutSec 5).Content
Write-Host 'MODELS COUNT:'
try {
    $m = (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/local-models' -TimeoutSec 10).Content | ConvertFrom-Json
    if ($m.models) { $m.models.Count } else { 'no models key' }
} catch { $_.Exception.Message }
Write-Host 'PROCESSES:'
Get-Process 'DFlash Console' -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count
