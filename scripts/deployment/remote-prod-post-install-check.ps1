$ErrorActionPreference = 'Continue'
Write-Host 'MODEL_DIRS:' (Get-ChildItem 'C:\Users\afars\DFlash Console\models' -Directory -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host 'INSTALL_LOG:'
Get-Content 'C:\Users\developer\dflash-03147-install.log' -ErrorAction SilentlyContinue | Select-Object -Last 20
Write-Host 'HEALTH:'
try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/health' -TimeoutSec 5).Content } catch { $_.Exception.Message }
Write-Host 'MODELS_API:'
try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/models' -TimeoutSec 10).Content.Substring(0, [Math]::Min(500, ((Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/models' -TimeoutSec 10).Content.Length))) } catch { $_.Exception.Message }
