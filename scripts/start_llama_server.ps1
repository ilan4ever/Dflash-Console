<#
.SYNOPSIS
    Start llama-server with named speed profiles for this dual-GPU machine.
.DESCRIPTION
    Profiles tune for single-user chatbot latency on GPU0 (RTX 4090 D).
    Avoids dual-GPU tensor-split for chat (that hurts single-request tok/s).

.PARAMETER Profile
    gemma-chat   - Gemma 4 31B + DFlash (default chatbot winner)
    gemma-12-ar   - Gemma 4 12B autoregressive (mediator-friendly, port 8092)
    gemma-12-dflash - Gemma 4 12B + DFlash draft (port 8092)
    qwen-dflash  - Qwen3.5-27B + upstream DFlash
    qwen-ar      - Qwen3.5-27B autoregressive only
    bonsai       - Ternary-Bonsai-27B AR (uses bonsai-27b CUDA binary)
    bonsai-spec  - Ternary-Bonsai-27B + dspark draft (no prompt-cache reuse)
.PARAMETER Port
    Override listen port (profile defaults below).
.PARAMETER ContextSize
    Context tokens. Default 65536 for gemma profiles (16384 for bonsai-spec, 8192 for others).
.PARAMETER IdleUnloadSeconds
    Unload model from GPU/RAM after this many seconds with no chat traffic.
    Uses llama-server --sleep-idle-seconds. Default: 3600 (1 hour).
    Set 0 or -1 to keep the model loaded forever.
#>

param(
    [ValidateSet("gemma-chat", "gemma-ar", "gemma-12-ar", "gemma-12-dflash", "qwen-dflash", "qwen-ar", "bonsai", "bonsai-spec")]
    [string]$Profile = "gemma-chat",
    [int]$Port = 0,
    [int]$ContextSize = 0,
    [string]$HostAddress = "127.0.0.1",
    [int]$IdleUnloadSeconds = 3600,
    [int]$MainGpu = 0,
    [ValidateSet("none", "layer", "row")]
    [string]$SplitMode = "none",
    [string]$TensorSplit = "",
    [int]$GpuLayers = 99,
    [int]$CpuThreads = 9,
    [int]$EvalBatch = 2048,
    [int]$PhysicalBatch = 512,
    [ValidateSet("on", "off")]
    [string]$FlashAttention = "on",
    [ValidateSet("on", "off")]
    [string]$KvOffload = "on",
    [string]$ModelsPreset = "",
    [int]$Parallel = 0,
    [ValidateSet("auto", "none", "low", "medium", "high", "max")]
    [string]$ReasoningEffort = "auto",
    [switch]$RouterMode
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $PSCommandPath
$RepoRoot = Resolve-Path "$ScriptDir\.."
$BonsaiRoot = Join-Path $RepoRoot "bonsai-27b"
$MainBin = Join-Path $RepoRoot "llama.cpp\build\bin\Release\llama-server.exe"
$BonsaiBin = Join-Path $BonsaiRoot "bin\cuda\llama-server.exe"

$GemmaTarget = Join-Path $RepoRoot "models\google\gemma-4-31B-it-qat-q4_0-gguf\gemma-4-31B_q4_0-it.gguf"
if (-not (Test-Path $GemmaTarget)) {
    $GemmaTarget = Join-Path $env:USERPROFILE ".lmstudio\models\google\gemma-4-31B-it-qat-q4_0-gguf\gemma-4-31B_q4_0-it.gguf"
}
$GemmaDraft = Join-Path $RepoRoot "models\gemma-draft\gemma-4-31B-it-DFlash-Q4_K_M.gguf"
$Gemma12Draft = Join-Path $RepoRoot "models\gemma-draft\gemma-4-12B-it-DFlash-Q4_K_M.gguf"
$Gemma12Target = $null
foreach ($candidate in @(
    (Join-Path $RepoRoot "models\gemma-4-12b-it\gemma-4-12B-it-Q4_K_M.gguf"),
    (Join-Path $RepoRoot "models\google\gemma-4-12b-it-qat-q4_0-gguf\gemma-4-12b-it-qat-q4_0.gguf"),
    (Join-Path $env:USERPROFILE ".lmstudio\models\google\gemma-4-12B-it-qat-q4_0-gguf\gemma-4-12B_q4_0-it.gguf"),
    (Join-Path $env:USERPROFILE ".lmstudio\models\google\gemma-4-12b-it-qat-q4_0-gguf\gemma-4-12b_q4_0-it.gguf")
)) {
    if (Test-Path $candidate) { $Gemma12Target = $candidate; break }
}
if (-not $Gemma12Target) {
    $gemma12Hits = Get-ChildItem -Path (Join-Path $env:USERPROFILE ".lmstudio\models\google") -Recurse -Filter "*.gguf" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '12[Bb].*gemma|gemma.*12[Bb]' } |
        Select-Object -First 1
    if ($gemma12Hits) { $Gemma12Target = $gemma12Hits.FullName }
}
$QwenTarget = Join-Path $RepoRoot "models\Qwen3.5-27B-Q4_K_M.gguf"
$QwenDraft = Join-Path $RepoRoot "models\Qwen3.5-27B-DFlash-F16.gguf"
$BonsaiTarget = Join-Path $BonsaiRoot "models\ternary-gguf\27B\Ternary-Bonsai-27B-Q2_0.gguf"
$BonsaiDraft = Join-Path $BonsaiRoot "models\ternary-gguf\27B\Ternary-Bonsai-27B-dspark-Q4_1.gguf"

# Translate a reasoning effort level into llama-server CLI args.
#   auto   -> no flags (llama-server template default)
#   none   -> --reasoning off
#   low    -> --reasoning on --reasoning-budget 512
#   medium -> --reasoning on --reasoning-budget 2048
#   high   -> --reasoning on --reasoning-budget 8192
#   max    -> --reasoning on --reasoning-budget -1 (unrestricted)
function Get-ReasoningArgs {
    param(
        [ValidateSet("auto", "none", "low", "medium", "high", "max")]
        [string]$ReasoningEffort = "auto"
    )
    if ($ReasoningEffort -eq "none") {
        return @("--reasoning", "off")
    }
    if ($ReasoningEffort -eq "auto") {
        return @()
    }
    $budget = switch ($ReasoningEffort) {
        "low" { 512 }
        "medium" { 2048 }
        "high" { 8192 }
        "max" { -1 }
        default { -1 }
    }
    return @("--reasoning", "on", "--reasoning-budget", "$budget")
}

if ($RouterMode) {
    if ([string]::IsNullOrWhiteSpace($ModelsPreset)) {
        Write-Error "RouterMode requires -ModelsPreset"
        exit 1
    }
    if (-not (Test-Path $ModelsPreset)) {
        Write-Error "Models preset not found: $ModelsPreset"
        exit 1
    }
    if ($Port -le 0) { $Port = 8090 }
    if ($ContextSize -le 0) { $ContextSize = 65536 }
    if ($GpuLayers -le 0) { $GpuLayers = 99 }
    if ($CpuThreads -le 0) { $CpuThreads = 9 }
    if ($EvalBatch -le 0) { $EvalBatch = 2048 }
    if ($PhysicalBatch -le 0) { $PhysicalBatch = 512 }
    if ($Parallel -le 0) { $Parallel = 4 }
    $routerArgs = @(
        "--host", $HostAddress,
        "--port", "$Port",
        "-c", "$ContextSize",
        "-np", "$Parallel",
        "-ngl", "$GpuLayers",
        "-t", "$CpuThreads",
        "-fa", $FlashAttention,
        "-b", "$EvalBatch",
        "-ub", "$PhysicalBatch",
        "--main-gpu", "$MainGpu",
        "--split-mode", $SplitMode,
        "--models-preset", $ModelsPreset,
        "--no-models-autoload",
        "--jinja"
    )
    if ($KvOffload -eq "off") {
        $routerArgs += "--no-kv-offload"
    } else {
        $routerArgs += "--kv-offload"
    }
    if ($IdleUnloadSeconds -gt 0) {
        $routerArgs += @("--sleep-idle-seconds", "$IdleUnloadSeconds")
    }
    if ($ReasoningEffort -ne "auto") {
        $routerArgs += (Get-ReasoningArgs -ReasoningEffort $ReasoningEffort)
    }
    Write-Host "=== llama-server router ===" -ForegroundColor Cyan
    Write-Host "Binary: $MainBin"
    Write-Host "Port:   $Port   Preset: $ModelsPreset"
    Write-Host "API:    http://${HostAddress}:${Port}/v1" -ForegroundColor Green
    & $MainBin @routerArgs
    exit $LASTEXITCODE
}

$defaults = @{
    "gemma-chat"  = @{ Port = 8090; Ctx = 65536; Bin = $MainBin }
    "gemma-ar"    = @{ Port = 8090; Ctx = 65536; Bin = $MainBin }
    "gemma-12-ar" = @{ Port = 8092; Ctx = 65536; Bin = $MainBin }
    "gemma-12-dflash" = @{ Port = 8092; Ctx = 65536; Bin = $MainBin }
    "qwen-dflash" = @{ Port = 8091; Ctx = 8192; Bin = $MainBin }
    "qwen-ar"     = @{ Port = 8091; Ctx = 8192; Bin = $MainBin }
    "bonsai"      = @{ Port = 8082; Ctx = 8192; Bin = $BonsaiBin }
    "bonsai-spec" = @{ Port = 8082; Ctx = 16384; Bin = $BonsaiBin }
}

$cfg = $defaults[$Profile]
if ($Port -le 0) { $Port = $cfg.Port }
if ($ContextSize -le 0) { $ContextSize = $cfg.Ctx }
$ServerBin = $cfg.Bin

if (-not (Test-Path $ServerBin)) {
    Write-Error "llama-server not found: $ServerBin"
    exit 1
}

# Bonsai CUDA build needs its DLL directory on PATH
if ($ServerBin -like "*bonsai-27b*") {
    $env:Path = "$(Split-Path $ServerBin -Parent);$env:Path"
}

if ($GpuLayers -le 0) { $GpuLayers = 99 }
if ($CpuThreads -le 0) { $CpuThreads = 9 }
if ($EvalBatch -le 0) { $EvalBatch = 2048 }
if ($PhysicalBatch -le 0) { $PhysicalBatch = 512 }

$argsList = @(
    "--host", $HostAddress,
    "--port", "$Port",
    "-c", "$ContextSize",
    "-np", "1",
    "-ngl", "$GpuLayers",
    "-t", "$CpuThreads",
    "-fa", $FlashAttention,
    "-b", "$EvalBatch",
    "-ub", "$PhysicalBatch",
    "--main-gpu", "$MainGpu",
    "--split-mode", $SplitMode,
    "--jinja",
    "--mlock"
)

if ($KvOffload -eq "off") {
    $argsList += "--no-kv-offload"
} else {
    $argsList += "--kv-offload"
}

if ($SplitMode -ne "none" -and -not [string]::IsNullOrWhiteSpace($TensorSplit)) {
    $argsList += @("--tensor-split", $TensorSplit)
}

# Auto-unload when idle: frees VRAM/RAM; next chat request reloads the model.
if ($IdleUnloadSeconds -gt 0) {
    $argsList += @("--sleep-idle-seconds", "$IdleUnloadSeconds")
}

# Reasoning/thinking control (only wired for profiles that ship a thinking
# template; the Console passes -ReasoningEffort from the runtime panel).
if ($ReasoningEffort -ne "auto") {
    $argsList += (Get-ReasoningArgs -ReasoningEffort $ReasoningEffort)
}

switch ($Profile) {
    "gemma-chat" {
        foreach ($p in @($GemmaTarget, $GemmaDraft)) {
            if (-not (Test-Path $p)) { Write-Error "Missing model: $p"; exit 1 }
        }
        $argsList = @("-m", $GemmaTarget, "-md", $GemmaDraft,
            "--spec-type", "draft-dflash", "--spec-draft-n-max", "8",
            "--cache-type-k", "q4_0", "--cache-type-v", "q4_0",
            "-a", "gemma-4-31b-it-dflash") + $argsList
        $endpointNote = "Chatbot winner: http://${HostAddress}:${Port}/v1  (Gemma 4 31B DFlash)"
    }
    "gemma-ar" {
        if (-not (Test-Path $GemmaTarget)) { Write-Error "Missing model: $GemmaTarget"; exit 1 }
        $argsList = @("-m", $GemmaTarget,
            "--cache-type-k", "q4_0", "--cache-type-v", "q4_0",
            "-a", "gemma-4-31b-it-dflash") + $argsList
        $endpointNote = "http://${HostAddress}:${Port}/v1  (Gemma 4 31B AR)"
    }
    "gemma-12-ar" {
        if (-not $Gemma12Target -or -not (Test-Path $Gemma12Target)) {
            Write-Error "Missing Gemma 4 12B model under $env:USERPROFILE\.lmstudio\models\google"
            exit 1
        }
        $argsList = @("-m", $Gemma12Target,
            "--cache-type-k", "q4_0", "--cache-type-v", "q4_0",
            "-a", "gemma-4-12b-it-qat") + $argsList
        $endpointNote = "http://${HostAddress}:${Port}/v1  (Gemma 4 12B AR / mediator)"
    }
    "gemma-12-dflash" {
        if (-not $Gemma12Target -or -not (Test-Path $Gemma12Target)) {
            Write-Error "Missing Gemma 4 12B model under $env:USERPROFILE\.lmstudio\models\google"
            exit 1
        }
        if (-not (Test-Path $Gemma12Draft)) {
            Write-Error "Missing DFlash draft: $Gemma12Draft"
            exit 1
        }
        $argsList = @("-m", $Gemma12Target, "-md", $Gemma12Draft,
            "--spec-type", "draft-dflash", "--spec-draft-n-max", "8",
            "--cache-type-k", "q4_0", "--cache-type-v", "q4_0",
            "-a", "gemma-4-12b-it-qat") + $argsList
        $endpointNote = "http://${HostAddress}:${Port}/v1  (Gemma 4 12B DFlash)"
    }
    "qwen-dflash" {
        foreach ($p in @($QwenTarget, $QwenDraft)) {
            if (-not (Test-Path $p)) { Write-Error "Missing model: $p"; exit 1 }
        }
        $argsList = @("-m", $QwenTarget, "-md", $QwenDraft,
            "--spec-type", "draft-dflash", "--spec-draft-n-max", "8",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0") + $argsList
        $endpointNote = "http://${HostAddress}:${Port}/v1  (Qwen3.5-27B DFlash)"
    }
    "qwen-ar" {
        if (-not (Test-Path $QwenTarget)) { Write-Error "Missing model: $QwenTarget"; exit 1 }
        $argsList = @("-m", $QwenTarget,
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0") + $argsList
        $endpointNote = "http://${HostAddress}:${Port}/v1  (Qwen3.5-27B AR)"
    }
    "bonsai" {
        if (-not (Test-Path $BonsaiTarget)) { Write-Error "Missing model: $BonsaiTarget"; exit 1 }
        $argsList = @("-m", $BonsaiTarget, "--reasoning-budget", "0") + $argsList
        $endpointNote = "http://${HostAddress}:${Port}/v1  (Bonsai Ternary-27B AR)"
    }
    "bonsai-spec" {
        foreach ($p in @($BonsaiTarget, $BonsaiDraft)) {
            if (-not (Test-Path $p)) { Write-Error "Missing model: $p"; exit 1 }
        }
        $argsList = @("-m", $BonsaiTarget, "-md", $BonsaiDraft,
            "--spec-type", "draft-dspark", "--spec-draft-n-max", "4",
            "-ngld", "999", "--reasoning-budget", "0") + $argsList
        $endpointNote = "http://${HostAddress}:${Port}/v1  (Bonsai + dspark; no multi-turn KV reuse)"
    }
}

Write-Host "=== llama-server profile: $Profile ===" -ForegroundColor Cyan
Write-Host "Binary: $ServerBin"
Write-Host "Port:   $Port   Context: $ContextSize   GPU: $MainGpu   Split: $SplitMode   ngl: $GpuLayers   threads: $CpuThreads"
if ($IdleUnloadSeconds -gt 0) {
    $idleMin = [math]::Round($IdleUnloadSeconds / 60.0, 1)
    Write-Host "Idle:   unload after ${IdleUnloadSeconds}s (${idleMin} min) with no chat use" -ForegroundColor Yellow
} else {
    Write-Host "Idle:   disabled (model stays loaded)" -ForegroundColor DarkGray
}
Write-Host "API:    $endpointNote" -ForegroundColor Green
Write-Host ""

& $ServerBin @argsList
exit $LASTEXITCODE
