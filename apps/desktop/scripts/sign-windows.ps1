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
        $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction SilentlyContinue
        $machineStore = $false
        if (-not $certificate) {
            $certificate = Get-Item -LiteralPath "Cert:\LocalMachine\My\$CertificateThumbprint" -ErrorAction SilentlyContinue
            $machineStore = [bool]$certificate
        }
        if (-not $certificate -or -not $certificate.HasPrivateKey) { throw "The publisher certificate and its private key must be accessible in the Windows certificate store." }
        if ($certificate.NotAfter -le (Get-Date) -or $certificate.NotBefore -gt (Get-Date)) { throw "The publisher certificate is not currently valid." }
        $usages = @($certificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' } | ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
        if ('1.3.6.1.5.5.7.3.3' -notin $usages) { throw "The selected certificate is not a code-signing certificate." }
        $tool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        $toolPath = if ($tool) { $tool.Source } else { "" }
        if (-not $toolPath) {
            $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
            $toolPath = (Get-ChildItem -LiteralPath $kits -Directory | Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)
        }
        if (-not $toolPath) { throw "Install Windows SDK SignTool, or configure ECHOSPEAK_SIGN_SCRIPT." }
        $signArgs = @("sign", "/sha1", $CertificateThumbprint, "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256")
        if ($machineStore) { $signArgs += "/sm" }
        & $toolPath @signArgs $target
        if ($LASTEXITCODE -ne 0) { throw "SignTool failed." }
    } else { throw "Configure ECHOSPEAK_SIGN_CERT_SHA1 or ECHOSPEAK_SIGN_SCRIPT for publisher signing." }
}
$signature = Get-AuthenticodeSignature -LiteralPath $target
if ($signature.Status -ne "Valid" -or -not $signature.SignerCertificate) { throw "Publisher signature verification failed for $target ($($signature.Status))." }
if (-not $signature.TimeStamperCertificate) { throw "Publisher signature is missing a timestamp: $target" }
if ($CertificateThumbprint -and $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) { throw "Publisher certificate does not match the configured identity." }
Write-Host "Verified publisher signature: $(Split-Path -Leaf $target)"
