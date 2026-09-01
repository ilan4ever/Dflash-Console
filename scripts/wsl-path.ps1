function Convert-WindowsPathForWsl {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full -match '^([A-Za-z]):\\(.*)$') {
        return ('/mnt/{0}/{1}' -f $matches[1].ToLower(), ($matches[2] -replace '\\', '/'))
    }
    throw "Could not map Windows path into WSL: $Path"
}

function Invoke-WslBashScript {
    param(
        [Parameter(Mandatory)]
        [string]$Distro,
        [Parameter(Mandatory)]
        [string]$WslScriptPath
    )

    $output = New-Object System.Collections.Generic.List[string]
    $prevPref = $ErrorActionPreference
    $prevNative = $false
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
        $prevNative = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    $ErrorActionPreference = 'Continue'
    $exitCode = 1
    try {
        & wsl -d $Distro -- bash ($WslScriptPath.Trim()) 2>&1 | ForEach-Object {
            $line = if ($_ -is [System.Management.Automation.ErrorRecord]) {
                [string]$_.ToString()
            } else {
                [string]$_
            }
            if ($line) {
                [void]$output.Add($line)
                Write-Host $line
            }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevPref
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $prevNative
        }
    }
    return [pscustomobject]@{
        Output = $output
        ExitCode = $exitCode
    }
}
