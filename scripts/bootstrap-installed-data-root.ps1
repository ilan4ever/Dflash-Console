# Seed the per-user Console data root from the installed program bundle.
param(
    [Parameter(Mandatory = $true)][string]$ProgramRoot,
    [string]$DataRoot = (Join-Path $env:USERPROFILE 'DFlash Console')
)

$ErrorActionPreference = 'Stop'
$ProgramRoot = (Resolve-Path -LiteralPath $ProgramRoot).Path
$DataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$runtimeSrc = Join-Path $ProgramRoot 'resources\console-runtime'

if (-not (Test-Path -LiteralPath (Join-Path $runtimeSrc 'server.ps1'))) {
    throw "Console runtime bundle is missing under $runtimeSrc"
}

$items = @(
    'api', 'core', 'static', 'assets', 'scripts', 'runtime-bundles',
    'server.ps1', 'run.ps1', 'requirements.txt', 'requirements.lock', 'config.example.json'
)

New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
foreach ($item in $items) {
    $from = Join-Path $runtimeSrc $item
    if (-not (Test-Path -LiteralPath $from)) { continue }
    $to = Join-Path $DataRoot $item
    if (Test-Path -LiteralPath $from -PathType Container) {
        if (Test-Path -LiteralPath $to) { Remove-Item -LiteralPath $to -Recurse -Force }
        Copy-Item -LiteralPath $from -Destination $to -Recurse -Force
    } else {
        New-Item -ItemType Directory -Path (Split-Path -Parent $to) -Force | Out-Null
        Copy-Item -LiteralPath $from -Destination $to -Force
    }
}

foreach ($sub in @('models', 'logs', 'logs\presets')) {
    New-Item -ItemType Directory -Path (Join-Path $DataRoot $sub) -Force | Out-Null
}

$versionFile = Join-Path $runtimeSrc '.runtime-version'
if (Test-Path -LiteralPath $versionFile) {
    Copy-Item -LiteralPath $versionFile -Destination (Join-Path $DataRoot '.runtime-version') -Force
}

$configPath = Join-Path $DataRoot 'config.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    $examplePath = Join-Path $DataRoot 'config.example.json'
    $template = if (Test-Path -LiteralPath $examplePath) {
        Get-Content -LiteralPath $examplePath -Raw | ConvertFrom-Json
    } else {
        [pscustomobject]@{ ui_port = 8900; servers = @() }
    }
    $modelsDir = Join-Path $DataRoot 'models'
    $config = [ordered]@{
        ui_port = [int]($template.ui_port | ForEach-Object { if ($_ -gt 0) { $_ } else { 8900 } })
        dflash_root = $DataRoot
        models_root = $modelsDir
        setup_complete = $false
        servers = @()
        model_libraries = @(
            [ordered]@{
                id = 'dflash-checkpoints'
                label = 'DFlash Console models'
                path = $modelsDir
                enabled = $true
                preset = 'dflash'
                download_default = $true
            }
        )
    }
    foreach ($prop in $template.PSObject.Properties.Name) {
        if ($prop -notin @('ui_port', 'dflash_root', 'models_root', 'setup_complete', 'servers', 'model_libraries')) {
            $config[$prop] = $template.$prop
        }
    }
    ($config | ConvertTo-Json -Depth 8) + "`n" | Set-Content -LiteralPath $configPath -Encoding UTF8
}

Write-Output $DataRoot
