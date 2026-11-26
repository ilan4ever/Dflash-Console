# Give an installed Console folder normal Windows access.
# A low-integrity mark makes Python start restricted, and it then cannot
# write engine settings. Model weight files are not walked one by one;
# they inherit the folder mark instead.
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [ValidateSet('data', 'program')][string]$Kind = 'data',
    [switch]$Shared,
    [switch]$Recurse,
    # Comma-separated child names. With -Recurse, only these children are
    # walked. The app and setup pass the runtime items they just copied: the
    # Python environments (.venv, runtimes) hold tens of thousands of files,
    # were set when they were created, and walking them took over 2 minutes.
    [string]$Only = '',
    # Walk every file even when the folder already looks fine.
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Path = [System.IO.Path]::GetFullPath($Path)
if (-not (Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Invoke-Icacls {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$IcaclsArgs)
    & icacls.exe @IcaclsArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not set folder access ($LASTEXITCODE): $($IcaclsArgs -join ' ')"
    }
}

function Set-MediumIntegrity {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [switch]$Tree
    )
    $icaclsArgs = @($Target, '/setintegritylevel', '(OI)(CI)M', '/C', '/Q')
    if ($Tree) { $icaclsArgs += '/T' }
    Invoke-Icacls @icaclsArgs
}

function Grant-Access {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$Ace,
        [switch]$Tree
    )
    $icaclsArgs = @($Target, '/grant', $Ace, '/C', '/Q')
    if ($Tree) { $icaclsArgs += '/T' }
    Invoke-Icacls @icaclsArgs
}

function Get-CurrentUserSid {
    $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    if (-not $account) { throw 'Could not read the current user.' }
    return $account.Value
}

function Test-LowIntegrity([string]$Target) {
    $listing = (& icacls.exe $Target 2>$null | Out-String)
    return $listing -match 'Low Mandatory Level'
}

function Test-UserCanModify([string]$Target, [string]$Sid) {
    try { $acl = Get-Acl -LiteralPath $Target } catch { return $false }
    $modify = [System.Security.AccessControl.FileSystemRights]::Modify
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne 'Allow') { continue }
        try {
            $ruleSid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
        } catch { continue }
        if ($ruleSid -eq $Sid -and (($rule.FileSystemRights -band $modify) -eq $modify)) { return $true }
    }
    return $false
}

$userSid = Get-CurrentUserSid

# The full walk below re-stamps every file (engine environments hold hundreds
# of thousands), which made each update take minutes. It is a repair: run it
# only when the folder or one of its top-level folders is still restricted,
# or the user cannot modify it. -Force always walks, and an -Only walk always
# covers the items it names (they were just copied).
if ($Recurse -and $Kind -eq 'data' -and -not $Force -and -not $Only.Trim()) {
    $targets = @($Path) + @(
        Get-ChildItem -LiteralPath $Path -Force -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne 'models' } |
            ForEach-Object { $_.FullName }
    )
    $restricted = @($targets | Where-Object { Test-LowIntegrity $_ })
    if ($restricted.Count -eq 0 -and (Test-UserCanModify $Path $userSid)) {
        $Recurse = $false
    }
}

Set-MediumIntegrity -Target $Path
if ($Kind -eq 'program' -and $Shared) {
    # Everyone can run the program. Only an administrator can change it.
    Grant-Access -Target $Path -Ace '*S-1-5-32-545:(OI)(CI)RX'
    Grant-Access -Target $Path -Ace '*S-1-5-32-544:(OI)(CI)F'
    Grant-Access -Target $Path -Ace '*S-1-5-18:(OI)(CI)F'
} else {
    Grant-Access -Target $Path -Ace "*${userSid}:(OI)(CI)M"
}

if ($Recurse) {
    $children = @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue)
    $onlyNames = @($Only -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($onlyNames.Count -gt 0) {
        $children = @($children | Where-Object { $onlyNames -contains $_.Name })
    }
    foreach ($child in $children) {
        # Weight files stay as they are. New downloads inherit this folder.
        if ($child.PSIsContainer -and $child.Name -eq 'models') {
            Set-MediumIntegrity -Target $child.FullName
            continue
        }
        Set-MediumIntegrity -Target $child.FullName -Tree
        if ($Kind -eq 'program' -and $Shared) {
            Grant-Access -Target $child.FullName -Ace '*S-1-5-32-545:(OI)(CI)RX' -Tree
        } else {
            Grant-Access -Target $child.FullName -Ace "*${userSid}:(OI)(CI)M" -Tree
        }
    }
}

$listing = (& icacls.exe $Path | Out-String)
if ($listing -match 'Low Mandatory Level') {
    throw "Folder is still restricted: $Path"
}
