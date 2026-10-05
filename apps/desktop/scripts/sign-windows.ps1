param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string]$CertificateThumbprint = $env:ECHOSPEAK_SIGN_CERT_SHA1,
    # Optional user-owned hook for hardware tokens/cloud signing. It must accept -FilePath.
    [string]$SignScript = $env:ECHOSPEAK_SIGN_SCRIPT,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$VerifyOnly
)
$ErrorActionPreference = "Stop"
$target = (Resolve-Path -LiteralPath $FilePath).Path
if (-not $VerifyOnly) {
    if ($SignScript) {
        $hook = (Resolve-Path -LiteralPath $SignScript).Path
        $LASTEXITCODE = 0
        & $hook -FilePath $target
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "Publisher signing hook failed." }
    } elseif ($CertificateThumbprint) {
        if ($CertificateThumbprint -notmatch '^[a-fA-F0-9]{40}$') { throw "Use the SHA-1 thumbprint of your installed publisher certificate." }
        $tool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        $toolPath = if ($tool) { $tool.Source } else { "" }
        if (-not $toolPath) {
            $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
            $toolPath = (Get-ChildItem -LiteralPath $kits -Directory | Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)
        }
        if (-not $toolPath) { throw "Install Windows SDK SignTool, or configure ECHOSPEAK_SIGN_SCRIPT." }
        & $toolPath sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $target
        if ($LASTEXITCODE -ne 0) { throw "SignTool failed." }
    } else { throw "Configure ECHOSPEAK_SIGN_CERT_SHA1 or ECHOSPEAK_SIGN_SCRIPT for publisher signing." }
}
$signature = Get-AuthenticodeSignature -LiteralPath $target
if ($signature.Status -ne "Valid" -or -not $signature.SignerCertificate) { throw "Publisher signature verification failed for $target ($($signature.Status))." }
if (-not $signature.TimeStamperCertificate) { throw "Publisher signature is missing a timestamp: $target" }
if ($CertificateThumbprint -and $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) { throw "Publisher certificate does not match the configured identity." }
Write-Host "Verified publisher signature: $(Split-Path -Leaf $target)"
