# Give an installed Console folder normal Windows access.
# A low-integrity mark makes Python start restricted, and it then cannot
# write engine settings. Model weight files are not walked one by one;
# they inherit the folder mark instead.
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [ValidateSet('data', 'program')][string]$Kind = 'data',
    [switch]$Shared,
    [switch]$Recurse
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

Set-MediumIntegrity -Target $Path
$userSid = Get-CurrentUserSid
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
