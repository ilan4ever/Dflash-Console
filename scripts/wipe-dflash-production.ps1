# Wipe every DFlash Console trace from this Windows profile.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

# Never kill this script's own process tree — the command line contains the
# script path, which matches the DFlash pattern below.
$selfPids = @($PID)
try {
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction SilentlyContinue
    while ($parent -and $parent.ParentProcessId) {
        $selfPids += [int]$parent.ParentProcessId
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($parent.ParentProcessId)" -ErrorAction SilentlyContinue
    }
} catch {}

$names = @(
    'DFlash Console',
    'DFlash-Console-Setup-0*',
    'dflash-setup-ui',
    'dflash-console-python',
    'python'
)
Get-CimInstance Win32_Process | Where-Object {
    $selfPids -notcontains [int]$_.ProcessId -and {
        $cmd = [string]$_.CommandLine
        $exe = [string]$_.ExecutablePath
        ($cmd + ' ' + $exe) -match 'DFlash|dflash-setup|7z[A-F0-9]{6,}\\DFlash' -or
        # The Console API runs as "python -m uvicorn api.app:app" with no
        # DFlash string in its command line — match its signature too.
        $cmd -match 'uvicorn\s+api\.app:app'
    }.Invoke()
} | ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
}

$roots = @(
    "$env:LOCALAPPDATA\Programs\DFlash Console",
    "${env:ProgramFiles}\DFlash Console",
    "${env:ProgramFiles(x86)}\DFlash Console",
    "$env:USERPROFILE\DFlash Console",
    "$env:APPDATA\DFlash Console",
    "$env:LOCALAPPDATA\dflash-console",
    "$env:LOCALAPPDATA\Temp\DFlash-Console-updates",
    "$env:TEMP\DFlash-Console-updates"
)
foreach ($p in $roots) {
    if ($p -and (Test-Path -LiteralPath $p)) {
        Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Get-ChildItem "$env:TEMP" -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like '7z*' } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

@(
    "$env:TEMP\dflash-setup-ui.exe",
    "$env:TEMP\dflash-setup-ui-uninstall.exe",
    "$env:TEMP\dflash-setup-ui.log",
    "$env:TEMP\dflash-install-done.flag"
) | ForEach-Object {
    if (Test-Path -LiteralPath $_) { Remove-Item -LiteralPath $_ -Force -ErrorAction SilentlyContinue }
}

$links = @(
    "$env:USERPROFILE\Desktop\DFlash Console.lnk",
    "$env:PUBLIC\Desktop\DFlash Console.lnk",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\DFlash Console.lnk",
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\DFlash Console.lnk"
)
foreach ($l in $links) {
    if (Test-Path -LiteralPath $l) { Remove-Item -LiteralPath $l -Force -ErrorAction SilentlyContinue }
}

foreach ($hive in @('HKCU', 'HKLM')) {
    $key = "$hive\Software\Microsoft\Windows\CurrentVersion\Uninstall\DFlashConsole"
    cmd /c "reg delete `"$key`" /f" | Out-Null
}

Write-Output 'WIPED'
Get-ChildItem @(
    "$env:LOCALAPPDATA\Programs\DFlash Console",
    "$env:USERPROFILE\DFlash Console",
    "$env:APPDATA\DFlash Console"
) -ErrorAction SilentlyContinue | Select-Object FullName
Get-CimInstance Win32_Process | Where-Object {
    ([string]$_.ExecutablePath + [string]$_.CommandLine) -match 'DFlash'
} | Select-Object ProcessId, Name, ExecutablePath
