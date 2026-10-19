#Requires -Version 7.0
<#
.SYNOPSIS
    Pre-fills the Cline extension with the DFlash Console OpenAI-compatible gateway.

.DESCRIPTION
    Cline (saoudrizwan.claude-dev) keeps its live state under ~/.cline/data:
      - settings/providers.json  per-provider settings (base URL, key, model)
      - globalState.json         selected provider/model info/reasoning per mode
      - secrets.json             API keys
      - settings/models.json     per-model overrides (created when missing)

    This script points Cline's "OpenAI Compatible" provider at the DFlash Console
    gateway, selects a model and seeds the model configuration so the settings
    form is already filled in. Load the Continue-style model list once from the
    gateway (GET <gateway>/v1/models) so the Model ID dropdown is populated by
    Cline itself.

    Run it again whenever you want to change the default model.

.EXAMPLE
    ./scripts/configure-cline-provider.ps1

.EXAMPLE
    ./scripts/configure-cline-provider.ps1 -Model gemma-4-31b-q4-0-it -ContextWindow 65576
#>
param(
    [string]$GatewayUrl = 'http://127.0.0.1:8001',
    [string]$ApiKey = 'dflash-console',
    [string]$Model = 'qwen3-8-27b-q6-k-l',
    [int]$ContextWindow = 32768,
    [int]$MaxTokens = 4096,
    [int]$InputPrice = 0,
    [int]$OutputPrice = 0,
    [string]$ReasoningEffort = 'none',
    [string]$DataRoot = (Join-Path $env:USERPROFILE '.cline\data'),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Get-GatewayBaseUrl {
    param([string]$BaseUrl)
    $base = $BaseUrl.TrimEnd('/')
    if ($base -notmatch '/v1$') { $base = "$base/v1" }
    return $base
}

function Read-JsonFile {
    param([string]$Path, [System.Collections.IDictionary]$Default)
    if (-not (Test-Path -LiteralPath $Path)) { return $Default }
    $raw = Get-Content -LiteralPath $Path -Raw
    if ([string]::IsNullOrWhiteSpace($raw)) { return $Default }
    return ($raw | ConvertFrom-Json -AsHashtable)
}

function Save-JsonFile {
    param([string]$Path, [object]$Value)
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $json = $Value | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.UTF8Encoding]::new($false))
}

$base = Get-GatewayBaseUrl -BaseUrl $GatewayUrl

# Fail early if the gateway is not answering, but keep going: the user may start
# the Console later and the saved config will still be correct.
try {
    $models = (Invoke-RestMethod -Uri "$base/models" -TimeoutSec 10).data
    $ids = @($models | ForEach-Object { [string]$_.id })
    if ($ids -notcontains $Model) {
        Write-Warning "Model '$Model' is not in the gateway list. Available: $($ids -join ', ')"
    } else {
        Write-Host "Gateway reachable: $($ids.Count) model(s), '$Model' is registered."
    }
} catch {
    Write-Warning "Could not read $base/models: $($_.Exception.Message)"
}

$providersPath = Join-Path $DataRoot 'settings\providers.json'
$globalStatePath = Join-Path $DataRoot 'globalState.json'
$secretsPath = Join-Path $DataRoot 'secrets.json'

$providers = Read-JsonFile -Path $providersPath -Default @{ version = 1; modes = @{}; providers = @{} }
$globalState = Read-JsonFile -Path $globalStatePath -Default @{}
$secrets = Read-JsonFile -Path $secretsPath -Default @{}

if (-not $providers['version']) { $providers['version'] = 1 }
if (-not $providers['modes']) { $providers['modes'] = @{} }
if (-not $providers['providers']) { $providers['providers'] = @{} }

$providerSettings = [ordered]@{
    provider      = 'openai-compatible'
    apiKey        = $ApiKey
    baseUrl       = $base
    model         = $Model
    contextWindow = $ContextWindow
    maxTokens     = $MaxTokens
    # Ask the gateway for a duplicate-free model list (one id per engine);
    # without it the picker also shows *-dflash / dotted / gpt-4o aliases.
    headers       = [ordered]@{
        'X-DFlash-Compact-Models' = '1'
    }
    reasoning     = [ordered]@{
        enabled = ($ReasoningEffort -ne 'none')
        effort  = $ReasoningEffort
    }
}

$providers['providers']['openai-compatible'] = [ordered]@{
    settings    = $providerSettings
    updatedAt   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    tokenSource = 'manual'
}
$providers['lastUsedProvider'] = 'openai-compatible'

# Cline reads this through the legacy apiConfiguration too, so mirror the values
# the extension itself writes when the form is saved.
$globalState['openAiBaseUrl'] = $base
$globalState['openAiHeaders'] = [ordered]@{
    'X-DFlash-Compact-Models' = '1'
}
$globalState['planModeApiProvider'] = 'openai'
$globalState['actModeApiProvider'] = 'openai'
$globalState['planModeOpenAiModelId'] = $Model
$globalState['actModeOpenAiModelId'] = $Model
$globalState['planModeOpenAiModelInfo'] = [ordered]@{
    maxTokens          = $MaxTokens
    contextWindow      = $ContextWindow
    supportsImages     = $true
    supportsPromptCache = $false
    inputPrice         = $InputPrice
    outputPrice        = $OutputPrice
}
$globalState['actModeOpenAiModelInfo'] = [ordered]@{
    maxTokens          = $MaxTokens
    contextWindow      = $ContextWindow
    supportsImages     = $true
    supportsPromptCache = $false
    inputPrice         = $InputPrice
    outputPrice        = $OutputPrice
}
$globalState['planModeReasoningEffort'] = $ReasoningEffort
$globalState['actModeReasoningEffort'] = $ReasoningEffort

$secrets['openAiApiKey'] = $ApiKey

if ($DryRun) {
    Write-Host '--- providers.json ---'
    ($providers | ConvertTo-Json -Depth 30) | Write-Host
    Write-Host '--- globalState.json (changed keys) ---'
    foreach ($key in @('openAiBaseUrl', 'planModeOpenAiModelId', 'actModeOpenAiModelId', 'planModeReasoningEffort', 'actModeReasoningEffort')) {
        Write-Host "$key = $($globalState[$key])"
    }
    Write-Host '--- secrets.json ---'
    Write-Host "openAiApiKey = $ApiKey"
    return
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
foreach ($path in @($providersPath, $globalStatePath, $secretsPath)) {
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination "$path.bak-$stamp" -Force
    }
}

Save-JsonFile -Path $providersPath -Value $providers
Save-JsonFile -Path $globalStatePath -Value $globalState
Save-JsonFile -Path $secretsPath -Value $secrets

Write-Host "Cline configured for DFlash Console ($base), model '$Model'."
Write-Host 'Reload the VS Code window (Developer: Reload Window) so Cline reloads its state.'
