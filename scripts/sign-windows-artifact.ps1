[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Artifact,
    [string]$CertificateLink = $env:WINDOWS_CSC_LINK,
    [string]$CertificatePassword = $env:WINDOWS_CSC_KEY_PASSWORD
)

$ErrorActionPreference = 'Stop'
$artifactPath = (Resolve-Path -LiteralPath $Artifact).Path
if (-not $CertificateLink) { throw 'WINDOWS_CSC_LINK is required.' }
if (-not $CertificatePassword) { throw 'WINDOWS_CSC_KEY_PASSWORD is required.' }

function Resolve-SignTool {
    $path = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($path) { return $path.Source }

    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $kitsRoot) {
        $candidate = Get-ChildItem -LiteralPath $kitsRoot -Recurse -Filter signtool.exe -File -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    throw 'signtool.exe was not found on this Windows runner.'
}

$certificatePath = $null
$temporaryCertificate = $false
try {
    if (Test-Path -LiteralPath $CertificateLink -PathType Leaf) {
        $certificatePath = (Resolve-Path -LiteralPath $CertificateLink).Path
    } else {
        $encoded = [string]$CertificateLink
        if ($encoded -match '^data:[^,]+,(.+)$') {
            $encoded = $Matches[1]
        }
        $encoded = ($encoded -replace '\s', '')
        try {
            $bytes = [Convert]::FromBase64String($encoded)
        } catch {
            throw 'WINDOWS_CSC_LINK must be a PFX path, base64 value, or data URL.'
        }
        $certificatePath = Join-Path ([IO.Path]::GetTempPath()) ('dflash-signing-' + [Guid]::NewGuid().ToString('N') + '.pfx')
        [IO.File]::WriteAllBytes($certificatePath, $bytes)
        $temporaryCertificate = $true
    }

    $signtool = Resolve-SignTool
    & $signtool sign /fd SHA256 /f $certificatePath /p $CertificatePassword /tr 'http://timestamp.digicert.com' /td SHA256 $artifactPath
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $artifactPath (exit code $LASTEXITCODE)." }

    $signature = Get-AuthenticodeSignature -LiteralPath $artifactPath
    if ($signature.Status -ne 'Valid') {
        throw "Windows signature validation failed for $artifactPath ($($signature.Status))."
    }
    Write-Host "Signed $artifactPath" -ForegroundColor Green
} finally {
    if ($temporaryCertificate -and $certificatePath) {
        Remove-Item -LiteralPath $certificatePath -Force -ErrorAction SilentlyContinue
    }
}
