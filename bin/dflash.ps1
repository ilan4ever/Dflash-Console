param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArgs
)

$Root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = $Root
if (-not $env:DFLASH_ROOT) {
    $env:DFLASH_ROOT = $Root
}
& python -m dflash_cli @CommandArgs
exit $LASTEXITCODE
