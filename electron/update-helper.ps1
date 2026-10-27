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

function Clear-InstallDoneFlag {
    $doneFlag = Join-Path $env:TEMP 'dflash-install-done.flag'
    try { if (Test-Path $doneFlag) { Remove-Item $doneFlag -Force -ErrorAction SilentlyContinue } } catch {}
}

function Wait-ForInstallComplete {
    param(
        [string]$TargetVersion = '',
        [int]$TimeoutSeconds = 900
    )
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

function Test-DflashPayloadInstaller {
    param([string]$Path)
    # New installers are the branded setup UI itself with the package appended
    # (magic + payload offset + payload length, searched near the end because a
    # code signature can be added after the payload).
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {
            $length = $stream.Length
            if ($length -lt 24) { return $false }
            $window = [int][Math]::Min($length, 2097152)
            $buffer = New-Object byte[] $window
            $stream.Seek($length - $window, [IO.SeekOrigin]::Begin) | Out-Null
            $read = 0
            while ($read -lt $window) {
                $n = $stream.Read($buffer, $read, $window - $read)
                if ($n -le 0) { break }
                $read += $n
            }
            $magic = [Text.Encoding]::ASCII.GetBytes('DFLASHPL')
            for ($i = $read - 24; $i -ge 0; $i--) {
                $match = $true
                for ($j = 0; $j -lt 8; $j++) {
                    if ($buffer[$i + $j] -ne $magic[$j]) { $match = $false; break }
                }
                if (-not $match) { continue }
                $offset = [BitConverter]::ToInt64($buffer, $i + 8)
                $payloadLength = [BitConverter]::ToInt64($buffer, $i + 16)
                if ($offset -le 0 -or $payloadLength -le 0) { continue }
                if (($offset + $payloadLength) -ne ($length - $window + $i)) { continue }
                return $true
            }
            return $false
        } finally {
            $stream.Dispose()
        }
    } catch {
        return $false
    }
}

function Start-UpdateInstaller {
    param(
        [string]$Path,
        [string]$TargetVersion = '',
        [string]$InstallRoot = ''
    )
    # Quote every value that can contain spaces (e.g. "...\Programs\DFlash
    # Console"): Start-Process joins the array with spaces and does not add
    # quotes, which used to split /InstallRoot into two arguments.
    $setupArgs = @("/Package=`"$Path`"")
    $existingExe = $null
    if ($InstallRoot) {
        $existingExe = Join-Path $InstallRoot 'DFlash Console.exe'
        $setupArgs += "/InstallRoot=`"$InstallRoot`""
        if (Test-Path -LiteralPath $existingExe) {
            $setupArgs += '/AutoInstall'
            Write-HelperLog "Existing install at $InstallRoot — silent in-place update"
        } else {
            Write-HelperLog "No install at $InstallRoot — setup UI will ask for scope"
        }
    }
    # Self-contained installers show their window immediately, so run them as
    # they are. Unpacking a wrapper first used to leave the desktop empty for
    # many seconds before anything appeared.
    if (Test-DflashPayloadInstaller -Path $Path) {
        Write-HelperLog 'Installer carries its own package; launching it directly (instant window)'
        return Start-Process -FilePath $Path -ArgumentList $setupArgs -PassThru
    }
    # Older releases shipped a 7z SFX that kept the branded setup UI at its
    # root as setup.exe (7zS2.sfx launches the first match of
    # start/run/install/setup/*.exe). Those artifacts are still handled here:
    # extract the UI and run it with /AutoInstall. Newer self-contained
    # installers return earlier and never reach this path.
    $bootstrapNames = @('setup.exe', 'dflash-setup-ui.exe')
    $sevenZip = Resolve-SevenZipExe
    if ($sevenZip) {
        $extractRoot = Join-Path $env:TEMP ("DFlash_Update_" + ($(if ($TargetVersion) { $TargetVersion } else { 'pkg' }) -replace '[^\w\.-]', '_'))
        try {
            if (Test-Path $extractRoot) { Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue }
            New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
            Write-HelperLog "Extracting setup UI bootstrap with 7-Zip to $extractRoot"
            $bootstrap = Start-Process -FilePath $sevenZip -ArgumentList (@('e', $Path, "-o$extractRoot", '-y', 'install-version.txt') + $bootstrapNames) -Wait -PassThru -WindowStyle Hidden
            $uiPath = $null
            foreach ($name in $bootstrapNames) {
                $candidate = Join-Path $extractRoot $name
                if (Test-Path -LiteralPath $candidate) { $uiPath = $candidate; break }
            }
            if ($bootstrap.ExitCode -le 2 -and $uiPath) {
                Write-HelperLog "Launching $([IO.Path]::GetFileName($uiPath)) /Package=... (silent in-place install)"
                return Start-Process -FilePath $uiPath -ArgumentList $setupArgs -WorkingDirectory $extractRoot -PassThru
            }
            Write-HelperLog "Bootstrap extract failed (exit $($bootstrap.ExitCode)); launching SFX installer directly"
        } catch {
            Write-HelperLog "Bootstrap path failed: $($_.Exception.Message)"
        }
    }
    Write-HelperLog 'Launching SFX installer directly'
    return Start-Process -FilePath $Path -PassThru
}

try {
    Write-HelperLog "Helper started. TargetVersion=$TargetVersion InstallerPath=$InstallerPath ParentPid=$ParentProcessId"

    # Check the artifact BEFORE the ready handshake: once the app quits there is
    # nothing left on screen to report a failure.
    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw 'Staged DFlash update artifact is missing.'
    }

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

    # Show the installer window FIRST. The setup UI paints its progress window
    # immediately and force-closes any leftover Console process before it
    # touches files, so it no longer has to wait for the old app's graceful
    # engine shutdown. Waiting here left the old app on screen for ~10 s
    # between the update popup closing and the setup window appearing.
    Clear-InstallDoneFlag
    $installer = Start-UpdateInstaller -Path $InstallerPath -TargetVersion $TargetVersion -InstallRoot $InstallRoot
    if (-not $installer) { throw 'Installer did not start.' }
    Write-HelperLog "Installer launched PID=$($installer.Id)"

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
