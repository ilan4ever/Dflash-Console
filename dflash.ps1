param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArgs
)

& "$PSScriptRoot\bin\dflash.ps1" @CommandArgs
exit $LASTEXITCODE
