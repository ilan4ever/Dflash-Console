$ErrorActionPreference = 'Stop'
$version = '0.3.147'
$installer = "C:\Users\developer\DFlash-Console-Setup-$version-x64.exe"
$installRoot = 'C:\Users\afars\AppData\Local\Programs\DFlash Console'
$dataRoot = 'C:\Users\afars\DFlash Console'
$log = "C:\Users\developer\dflash-$($version.Replace('.', ''))-install.log"

function Log([string]$msg) {
    $line = "$(Get-Date -Format o) $msg"
    Add-Content -Path $log -Value $line
    Write-Host $line
}

Log '=== preflight ==='
if (-not (Test-Path $installer)) { throw "Installer missing: $installer" }
Log "installer bytes: $((Get-Item $installer).Length)"
if (-not (Test-Path $dataRoot)) { throw "Data root missing (models): $dataRoot" }
Log "data root present: $dataRoot"

Log '=== stop DFlash / setup / port 8900 ==='
$ErrorActionPreference = 'Continue'
cmd.exe /c "taskkill /F /IM `"DFlash Console.exe`" /T" 2>$null | Out-Host
cmd.exe /c "taskkill /F /IM dflash-setup-ui.exe /T" 2>$null | Out-Host
$line = netstat -ano | findstr '127.0.0.1:8900' | findstr LISTENING
if ($line) {
    $portPid = ($line.Trim() -split '\s+')[-1]
    if ($portPid -match '^\d+$') {
        Log "killing listener on 8900 PID $portPid"
        cmd.exe /c "taskkill /F /PID $portPid /T" 2>$null | Out-Host
    }
}
$ErrorActionPreference = 'Stop'
Start-Sleep -Seconds 3

Log '=== run installer (silent, afars install path) ==='
$args = @('/S', '/AutoInstall', "/InstallRoot=`"$installRoot`"")
$proc = Start-Process -FilePath $installer -ArgumentList $args -PassThru -Wait
Log "installer exit code: $($proc.ExitCode)"

Start-Sleep -Seconds 3
$verFile = Join-Path $installRoot 'install-version.txt'
if (Test-Path $verFile) {
    Log ("installed shell version: " + (Get-Content $verFile -Raw).Trim())
} else {
    Log 'install-version.txt missing after install'
}

Log '=== bootstrap runtime into data root (preserve models/config) ==='
$bootstrap = Join-Path $installRoot 'resources\console-runtime\scripts\bootstrap-installed-data-root.ps1'
if (Test-Path $bootstrap) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -ProgramRoot $installRoot -DataRoot $dataRoot
    Log 'bootstrap complete'
} else {
    Log "bootstrap script missing at $bootstrap"
}

if (Test-Path (Join-Path $dataRoot 'core\version.py')) {
    $py = Get-Content (Join-Path $dataRoot 'core\version.py') -Raw
    if ($py -match 'APP_VERSION\s*=\s*["'']([^"'']+)["'']') {
        Log "data root API version: $($Matches[1])"
    }
}

Log '=== launch DFlash Console (--dflash-post-update) ==='
$exe = Join-Path $installRoot 'DFlash Console.exe'
if (Test-Path $exe) {
    Start-Process -FilePath $exe -ArgumentList '--dflash-post-update' -WorkingDirectory (Split-Path -Parent $exe)
    Log 'launched DFlash Console'
} else {
    Log "missing exe: $exe"
}

Log '=== health probe ==='
for ($i = 0; $i -lt 18; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/health' -TimeoutSec 5
        Log "health: $($r.Content)"
        break
    } catch {
        Log "health attempt $($i + 1): $($_.Exception.Message)"
        Start-Sleep -Seconds 5
    }
}

Log '=== verify models still present ==='
try {
    $m = (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8900/api/local-models' -TimeoutSec 15).Content | ConvertFrom-Json
    $count = if ($m.models) { @($m.models).Count } else { 0 }
    Log "local-models count: $count"
} catch {
    Log "local-models probe failed: $($_.Exception.Message)"
}

Log '=== done ==='
