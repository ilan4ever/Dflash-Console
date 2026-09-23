$ErrorActionPreference = 'Continue'
Write-Host '=== Host / time ===' -ForegroundColor Cyan
hostname
Get-Date -Format o

Write-Host '=== CPU / RAM ===' -ForegroundColor Cyan
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Host ("CPU load: {0}%" -f $cpu.LoadPercentage)
$os = Get-CimInstance Win32_OperatingSystem
$totalGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
$freeGb = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
Write-Host ("RAM: {0} GB free / {1} GB total" -f $freeGb, $totalGb)

Write-Host '=== Top 15 by CPU time ===' -ForegroundColor Cyan
Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name, Id, CPU, @{
    N = 'WS_GB'; E = { [math]::Round($_.WorkingSet64 / 1GB, 2) }
}, StartTime | Format-Table -AutoSize

Write-Host '=== AI / Console related processes ===' -ForegroundColor Cyan
$pattern = 'python|llama|DFlash|dflash|electron|whisper|piper|ollama|lmstudio|node'
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match $pattern } |
    ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
        $cmd = [string]$_.CommandLine
        if ($cmd.Length -gt 220) { $cmd = $cmd.Substring(0, 220) + '...' }
        [PSCustomObject]@{
            PID  = $_.ProcessId
            Name = $_.Name
            User = if ($owner.User) { "$($owner.Domain)\$($owner.User)" } else { '' }
            Cmd  = $cmd
        }
    } | Sort-Object PID | Format-Table -Wrap

Write-Host '=== Port 8900 listeners ===' -ForegroundColor Cyan
netstat -ano | findstr ':8900' | findstr LISTENING

Write-Host '=== GPU (nvidia-smi) ===' -ForegroundColor Cyan
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    nvidia-smi --query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total --format=csv,noheader
    Write-Host '--- compute apps ---'
    nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv,noheader 2>$null
} else {
    Write-Host 'nvidia-smi not found'
}

Write-Host '=== DFlash health ===' -ForegroundColor Cyan
try {
    $h = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/health' -TimeoutSec 3
    Write-Host $h.Content
} catch {
    Write-Host ('health unreachable: ' + $_.Exception.Message)
}
