param(
    [ValidateSet('patch', 'minor', 'major')]
    [string]$Part = 'patch',
    [string]$SetVersion = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$argsList = @('--part', $Part)
if ($SetVersion) {
    $argsList += @('--set', $SetVersion)
}

& python (Join-Path $PSScriptRoot 'bump_version.py') @argsList
exit $LASTEXITCODE
