$ErrorActionPreference = 'Stop'
$installer = 'C:\Users\developer\DFlash-Console-Setup-0.3.106-x64.exe'
$installRoot = 'C:\Users\afars\AppData\Local\Programs\DFlash Console'
$dataRoot = 'C:\Users\afars\DFlash Console'
$log = 'C:\Users\developer\dflash-106-install.log'

function Log([string]$msg) {
    $line = "$(Get-Date -Format o) $msg"
    Add-Content -Path $log -Value $line
    Write-Host $line
}

Log '=== preflight ==='
$ErrorActionPreference = 'Continue'
if (-not (Test-Path $installer)) { throw "Installer missing: $installer" }
Log "installer bytes: $((Get-Item $installer).Length)"

cmd.exe /c "taskkill /F /IM `"DFlash Console.exe`" /T" 1>$null 2>$null
cmd.exe /c "taskkill /F /IM dflash-setup-ui.exe /T" 1>$null 2>$null
$line = netstat -ano | findstr '127.0.0.1:8900' | findstr LISTENING
if ($line) {
    $pid = ($line.Trim() -split '\s+')[-1]
    if ($pid -match '^\d+$') { cmd.exe /c "taskkill /F /PID $pid /T" 1>$null 2>$null }
}
$ErrorActionPreference = 'Stop'

Log '=== run installer (silent, afars profile) ==='
$args = @(
    '/S',
    '/AutoInstall',
    "/InstallRoot=`"$installRoot`""
)
$proc = Start-Process -FilePath $installer -ArgumentList $args -PassThru -Wait
Log "installer exit code: $($proc.ExitCode)"

Start-Sleep -Seconds 3
if (Test-Path (Join-Path $installRoot 'install-version.txt')) {
    $ver = (Get-Content (Join-Path $installRoot 'install-version.txt') -Raw).Trim()
    Log "installed version: $ver"
} else {
    Log 'install-version.txt missing after install'
}

if (Test-Path (Join-Path $dataRoot 'core\version.py')) {
    $py = Get-Content (Join-Path $dataRoot 'core\version.py') -Raw
    if ($py -match 'APP_VERSION\s*=\s*["'']([^"'']+)["'']') {
        Log "data root API version: $($Matches[1])"
    }
}

Log '=== health probe ==='
for ($i = 0; $i -lt 12; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/health' -TimeoutSec 3
        Log "health: $($r.Content)"
        break
    } catch {
        Log "health attempt $($i+1): $($_.Exception.Message)"
        Start-Sleep -Seconds 5
    }
}

Log '=== done ==='
