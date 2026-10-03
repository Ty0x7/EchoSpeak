# One-time setup for in-app updates.
#
# Creates the signing key pair that proves an update really came from you, and
# writes the public half into tauri.conf.json so every build can verify updates.
# The private key stays on this PC (%USERPROFILE%\.tauri\echospeak.key). Back it
# up somewhere safe: if it is lost, installed copies can no longer be updated
# in place and users would need to reinstall once.
param(
    [string]$KeyPath = (Join-Path $HOME ".tauri\echospeak.key"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$DesktopRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ConfPath = Join-Path $DesktopRoot "src-tauri\tauri.conf.json"
$PubPath = "$KeyPath.pub"

if (-not (Test-Path -LiteralPath (Join-Path $DesktopRoot "node_modules"))) {
    npm --prefix $DesktopRoot ci
    if ($LASTEXITCODE -ne 0) { throw "Desktop dependency install failed." }
}

if ((Test-Path -LiteralPath $KeyPath) -and -not $Force) {
    Write-Host "Using the existing signing key at $KeyPath"
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path $KeyPath) | Out-Null
    Write-Host "Creating the update signing key. Pick a password and keep it: releases ask for it."
    $forceArgs = @()
    if ($Force) { $forceArgs = @("--force") }
    npm --prefix $DesktopRoot run tauri -- signer generate -w $KeyPath @forceArgs
    if ($LASTEXITCODE -ne 0) { throw "Key generation failed." }
}

if (-not (Test-Path -LiteralPath $PubPath)) { throw "Public key not found at $PubPath." }
$PublicKey = (Get-Content -LiteralPath $PubPath -Raw).Trim()

# Replace just the pubkey value; ConvertTo-Json would reformat (and escape) the whole file.
$text = Get-Content -LiteralPath $ConfPath -Raw
$pattern = '("pubkey"\s*:\s*)"[^"]*"'
if ($text -notmatch $pattern) { throw "plugins.updater.pubkey is missing from $ConfPath." }
$text = ([regex]$pattern).Replace($text, { param($m) $m.Groups[1].Value + '"' + $PublicKey + '"' }, 1)
[System.IO.File]::WriteAllText($ConfPath, $text, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "Done. The public key is now in apps\desktop\src-tauri\tauri.conf.json - commit that file."
Write-Host "Back up $KeyPath (and its password) somewhere safe."
