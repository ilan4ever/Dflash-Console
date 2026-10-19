#Requires -Version 7.0
<#
.SYNOPSIS
    Exposes every model registered in DFlash Console to the Continue extension.

.DESCRIPTION
    Reads the live model list from the DFlash Console OpenAI-compatible gateway
    (GET <GatewayUrl>/v1/models) and writes a marked provider block into the
    Continue configuration file (~/.continue/config.yaml by default).

    The block is delimited by markers and fully regenerated on every run, so it
    can be re-run whenever models/servers are added, renamed or removed.

.EXAMPLE
    ./scripts/sync-continue-dflash-provider.ps1

.EXAMPLE
    ./scripts/sync-continue-dflash-provider.ps1 -DryRun
#>
param(
    [string]$GatewayUrl = 'http://127.0.0.1:8001',
    [string]$ConfigPath = (Join-Path $env:USERPROFILE '.continue\config.yaml'),
    [string]$ApiKey = 'dflash-console',
    [switch]$SkipAliases,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$MarkerStart = '  # >>> DFLASH-CONSOLE PROVIDER (auto-generated) >>>'
$MarkerEnd = '  # <<< DFLASH-CONSOLE PROVIDER <<<'

function ConvertTo-YamlSingleQuoted {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Get-GatewayModels {
    param([string]$BaseUrl)

    $base = $BaseUrl.TrimEnd('/')
    if ($base -notmatch '/v1$') { $base = "$base/v1" }
    $uri = "$base/models"
    try {
        $response = Invoke-RestMethod -Uri $uri -TimeoutSec 15
    } catch {
        throw "Could not read the DFlash Console model list from $uri. Is the Console gateway running? ($($_.Exception.Message))"
    }
    if (-not $response -or -not $response.data) {
        throw "The gateway at $uri returned no models."
    }
    return @($response.data)
}

function New-ProviderBlock {
    param(
        [object[]]$Models,
        [string]$BaseUrl,
        [string]$Key,
        [switch]$ExcludeAliases
    )

    $base = $BaseUrl.TrimEnd('/')
    if ($base -notmatch '/v1$') { $base = "$base/v1" }

    $usedNames = @{}
    $lines = @(
        $MarkerStart,
        ('  # Generated from GET ' + $base + '/models — re-run scripts/sync-continue-dflash-provider.ps1 to refresh.'),
        '  # Do not edit entries inside this block.'
    )

    foreach ($model in $Models) {
        $id = [string]$model.id
        if (-not $id) { continue }

        $meta = $model.meta
        $aliasOf = if ($meta) { [string]$meta.alias_of } else { '' }
        if ($ExcludeAliases -and $aliasOf) { continue }

        $display = if ($model.name) { [string]$model.name } else { $id }
        # The id leads the label so the list is searchable by model id and every
        # entry is visibly part of the DFlash Console group.
        $name = "DFlash Console · $id"
        if ($aliasOf) { $name = "$name (alias)" }
        if ($usedNames.ContainsKey($name)) {
            $name = "$name · $display"
        }
        while ($usedNames.ContainsKey($name)) {
            $name = "$name · $display"
        }
        $usedNames[$name] = $true

        $isEmbedding = [bool]($meta -and $meta.embedding)
        $contextLength = if ($model.context_size) { [int]$model.context_size } else { 32768 }
        if ($isEmbedding -and $contextLength -lt 2048) { $contextLength = 2048 }

        if ($aliasOf) {
            $lines += "  # alias of $aliasOf"
        }
        $lines += "  - name: $(ConvertTo-YamlSingleQuoted $name)"
        $lines += '    provider: openai'
        $lines += "    model: $(ConvertTo-YamlSingleQuoted $id)"
        $lines += "    apiBase: $(ConvertTo-YamlSingleQuoted $base)"
        $lines += "    apiKey: $(ConvertTo-YamlSingleQuoted $Key)"
        $lines += "    contextLength: $contextLength"
        if ($meta -and $meta.reasoning) {
            # Reasoning engines return an empty completion when the token budget
            # runs out before the final answer, so keep a workable default.
            $lines += '    defaultCompletionOptions:'
            $lines += '      maxTokens: 4096'
        }
        $lines += '    roles:'
        if ($isEmbedding) {
            $lines += '      - embed'
        } else {
            foreach ($role in @('chat', 'edit', 'apply', 'summarize')) {
                $lines += "      - $role"
            }
        }
    }

    $lines += $MarkerEnd
    return $lines
}

function Remove-ExistingBlock {
    param([string[]]$Lines)

    $result = [System.Collections.Generic.List[string]]::new()
    $skipping = $false
    foreach ($line in $Lines) {
        if ($line.Trim() -eq $MarkerStart.Trim()) { $skipping = $true; continue }
        if ($skipping) {
            if ($line.Trim() -eq $MarkerEnd.Trim()) { $skipping = $false }
            continue
        }
        $result.Add($line)
    }
    return , $result.ToArray()
}

function Merge-ProviderBlockIntoModels {
    param(
        [string[]]$Lines,
        [string[]]$Block
    )

    $modelsIndex = -1
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match '^models:\s*$') { $modelsIndex = $i; break }
    }
    if ($modelsIndex -lt 0) {
        throw "No top-level 'models:' section found in the Continue config."
    }

    $insertAt = $Lines.Count
    for ($i = $modelsIndex + 1; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match '^\S') { $insertAt = $i; break }
    }

    while ($insertAt -gt ($modelsIndex + 1) -and [string]::IsNullOrWhiteSpace($Lines[$insertAt - 1])) {
        $insertAt--
    }

    $result = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $insertAt; $i++) { $result.Add($Lines[$i]) }
    foreach ($line in $Block) { $result.Add($line) }
    for ($i = $insertAt; $i -lt $Lines.Count; $i++) { $result.Add($Lines[$i]) }
    return , $result.ToArray()
}

$models = Get-GatewayModels -BaseUrl $GatewayUrl
$block = New-ProviderBlock -Models $models -BaseUrl $GatewayUrl -Key $ApiKey -ExcludeAliases:$SkipAliases

if ($DryRun) {
    $block | ForEach-Object { Write-Host $_ }
    Write-Host ''
    Write-Host "$((@($models | Where-Object { -not ($SkipAliases -and $_.meta.alias_of) })).Count) model(s) would be written to $ConfigPath"
    return
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Continue config not found: $ConfigPath"
}

$original = @(Get-Content -LiteralPath $ConfigPath)
$cleaned = Remove-ExistingBlock -Lines $original
$updated = Merge-ProviderBlockIntoModels -Lines $cleaned -Block $block

[System.IO.File]::WriteAllLines($ConfigPath, $updated, [System.Text.UTF8Encoding]::new($false))

$written = @($models | Where-Object { -not ($SkipAliases -and $_.meta.alias_of) }).Count
Write-Host "DFlash Console provider synced to $ConfigPath ($written model(s))."
Write-Host 'Reload the Continue extension (Developer: Reload Window) to pick up the new models.'

