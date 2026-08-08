[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [string]$TargetVersion = '',
    [string]$InstallRoot = '',
    [string]$ReadyFile = '',
    [string]$QuitReadyFile = '',
    [int]$ParentProcessId = 0
)

$ErrorActionPreference = 'Stop'
$LogPath = Join-Path ([IO.Path]::GetDirectoryName($InstallerPath)) 'helper.log'

function Write-HelperLog([string]$Message) {
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Resolve-SevenZipExe {
    foreach ($candidate in @(
        (Join-Path ${env:ProgramFiles} '7-Zip\7z.exe'),
        (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe')
    )) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Get-InstalledAppCandidates {
    param([string]$Root = '')
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($Root) { [void]$candidates.Add((Join-Path $Root 'DFlash Console.exe')) }
    [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\DFlash Console\DFlash Console.exe'))
    [void]$candidates.Add((Join-Path $env:ProgramFiles 'DFlash Console\DFlash Console.exe'))
    return @($candidates | Where-Object { $_ } | Select-Object -Unique)
}

function Test-InstallComplete {
    param([string]$TargetVersion = '')
    $doneFlag = Join-Path $env:TEMP 'dflash-install-done.flag'
    if (-not (Test-Path $doneFlag)) { return $false }
    try {
        $flagVersion = (Get-Content -Path $doneFlag -TotalCount 1 -ErrorAction Stop).Trim()
        if ([string]::IsNullOrWhiteSpace($flagVersion) -or $flagVersion -like 'install-failed*') {
            return $false
        }
        if ([string]::IsNullOrWhiteSpace($TargetVersion)) { return $true }
        return ($flagVersion -eq $TargetVersion -or $flagVersion.StartsWith("$TargetVersion."))
    } catch {
        return $false
    }
}

function Wait-ForInstallComplete {
    param(
        [string]$TargetVersion = '',
        [int]$TimeoutSeconds = 900
    )
    $doneFlag = Join-Path $env:TEMP 'dflash-install-done.flag'
    try { if (Test-Path $doneFlag) { Remove-Item $doneFlag -Force -ErrorAction SilentlyContinue } } catch {}
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-InstallComplete -TargetVersion $TargetVersion) {
            Write-HelperLog "Install completion confirmed for version $TargetVersion"
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-Relaunch {
    param([string]$Root = '')
    foreach ($candidate in (Get-InstalledAppCandidates -Root $Root)) {
        if (-not (Test-Path $candidate)) { continue }
        Write-HelperLog "Relaunching: $candidate"
        Start-Process -FilePath $candidate -ArgumentList '--dflash-post-update' -WorkingDirectory (Split-Path -Parent $candidate) | Out-Null
        return $true
    }
    return $false
}

function Start-UpdateInstaller {
    param(
        [string]$Path,
        [string]$TargetVersion = '',
        [string]$InstallRoot = ''
    )
    $setupArgs = @("/Package=$Path")
    if ($InstallRoot) { $setupArgs += "/InstallRoot=$InstallRoot" }
    $sevenZip = Resolve-SevenZipExe
    if ($sevenZip) {
        $extractRoot = Join-Path $env:TEMP ("DFlash_Update_" + ($(if ($TargetVersion) { $TargetVersion } else { 'pkg' }) -replace '[^\w\.-]', '_'))
        try {
            if (Test-Path $extractRoot) { Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue }
            New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
            Write-HelperLog "Extracting setup UI bootstrap with 7-Zip to $extractRoot"
            $bootstrap = Start-Process -FilePath $sevenZip -ArgumentList @('e', $Path, "-o$extractRoot", '-y', 'dflash-setup-ui.exe', 'install-version.txt') -Wait -PassThru -WindowStyle Hidden
            $uiPath = Join-Path $extractRoot 'dflash-setup-ui.exe'
            if ($bootstrap.ExitCode -le 2 -and (Test-Path $uiPath)) {
                Write-HelperLog 'Launching dflash-setup-ui.exe /Package=... (interactive install scope)'
                return Start-Process -FilePath $uiPath -ArgumentList $setupArgs -WorkingDirectory $extractRoot -PassThru
            }
            Write-HelperLog 'Bootstrap extract failed; launching SFX installer directly'
        } catch {
            Write-HelperLog "Bootstrap path failed: $($_.Exception.Message)"
        }
    }
    Write-HelperLog 'Launching SFX installer directly'
    return Start-Process -FilePath $Path -PassThru
}

try {
    Write-HelperLog "Helper started. TargetVersion=$TargetVersion InstallerPath=$InstallerPath ParentPid=$ParentProcessId"

    if ($ReadyFile) {
        Set-Content -LiteralPath $ReadyFile -Value '{"ready":true}' -Encoding UTF8
        Write-HelperLog 'Ready handshake written.'
    }

    if ($QuitReadyFile) {
        $deadline = [DateTime]::UtcNow.AddMinutes(10)
        while (-not (Test-Path -LiteralPath $QuitReadyFile)) {
            if ([DateTime]::UtcNow -gt $deadline) { throw 'DFlash update quit handshake timed out.' }
            Start-Sleep -Milliseconds 200
        }
        Write-HelperLog 'Quit handshake received.'
    }

    if ($ParentProcessId -gt 0) {
        try { Wait-Process -Id $ParentProcessId -Timeout 120 } catch {
            Write-HelperLog "Wait-Process ended: $($_.Exception.Message)"
        }
    }

    $processDeadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $processDeadline) {
        if (-not (Get-Process -Name 'DFlash Console' -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    $leftover = @(Get-Process -Name 'DFlash Console' -ErrorAction SilentlyContinue)
    if ($leftover.Count -gt 0) {
        Write-HelperLog "Force-closing $($leftover.Count) leftover DFlash Console process(es)."
        & taskkill.exe /F /IM 'DFlash Console.exe' /T | Out-Null
        Start-Sleep -Milliseconds 800
    }

    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw 'Staged DFlash update artifact is missing.'
    }

    $installer = Start-UpdateInstaller -Path $InstallerPath -TargetVersion $TargetVersion -InstallRoot $InstallRoot
    if (-not $installer) { throw 'Installer did not start.' }
    Write-HelperLog "Installer launched PID=$($installer.Id)"

    if (-not (Wait-ForInstallComplete -TargetVersion $TargetVersion)) {
        throw 'DFlash installer did not complete before timeout.'
    }

    Start-Sleep -Seconds 2
    $alreadyRunning = @(Get-Process -Name 'DFlash Console' -ErrorAction SilentlyContinue)
    if ($alreadyRunning.Count -gt 0) {
        Write-HelperLog "App already running (PIDs=$($alreadyRunning.Id -join ',')); skip helper relaunch"
    } elseif (Start-Relaunch -Root $InstallRoot) {
        Write-HelperLog 'Relaunch succeeded'
    } else {
        Write-HelperLog 'Relaunch target not found (setup UI may have already opened the app)'
    }
} catch {
    Write-HelperLog "ERROR: $($_.Exception.Message)"
    throw
}
