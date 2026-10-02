# Build a signed Windows release and (with -Publish) put it on GitHub Releases.
#
#   1. Builds the installer with update signing turned on.
#   2. Writes latest.json, the file installed copies check for updates.
#   3. Copies everything to release\v<version>\.
#   4. -Publish: creates the GitHub release v<version> with the installers and
#      latest.json attached (needs the GitHub CLI, `gh auth login` once).
#
# After that, every installed EchoSpeak shows "Update to <version>" in
# Settings > About. Bump the version in the four version files before the next
# release; the app only offers versions newer than its own.
param(
    [string]$PythonExecutable = "python",
    [string]$KeyPath = (Join-Path $HOME ".tauri\echospeak.key"),
    [string]$NotesPath = "",
    [string]$Repo = "Ty0x7/EchoSpeak",
    [switch]$Publish,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$DesktopRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $DesktopRoot "..\..")).Path
$ConfPath = Join-Path $DesktopRoot "src-tauri\tauri.conf.json"
$conf = Get-Content -LiteralPath $ConfPath -Raw | ConvertFrom-Json
$Version = [string]$conf.version
$Tag = "v$Version"

if (-not $conf.plugins.updater.pubkey) {
    Write-Host "Updates are not set up yet; running setup-updater-key.ps1 first."
    & (Join-Path $PSScriptRoot "setup-updater-key.ps1") -KeyPath $KeyPath
    $conf = Get-Content -LiteralPath $ConfPath -Raw | ConvertFrom-Json
}
if (-not (Test-Path -LiteralPath $KeyPath)) {
    throw "Signing key not found at $KeyPath. Run setup-updater-key.ps1 (or restore your backup)."
}

if (-not $NotesPath) { $NotesPath = Join-Path $RepoRoot "docs\releases\$Tag.md" }
if (-not (Test-Path -LiteralPath $NotesPath)) { throw "Release notes not found: $NotesPath" }

$BundleRoot = Join-Path $DesktopRoot "src-tauri\target\release\bundle"
if (-not $SkipBuild) {
    $env:TAURI_SIGNING_PRIVATE_KEY = $KeyPath
    if (-not $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) {
        $secure = Read-Host "Signing key password (press Enter if it has none)" -AsSecureString
        $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = [System.Net.NetworkCredential]::new("", $secure).Password
    }
    $overlay = Join-Path $env:TEMP "echospeak-release-config.json"
    Set-Content -LiteralPath $overlay -Value '{"bundle":{"createUpdaterArtifacts":true}}' -Encoding ascii
    & (Join-Path $PSScriptRoot "build-windows.ps1") -PythonExecutable $PythonExecutable -TauriConfigPath $overlay
    if ($LASTEXITCODE -ne 0) { throw "Build failed." }
}

$Setup = Get-ChildItem -LiteralPath (Join-Path $BundleRoot "nsis") -Filter "*_${Version}_*-setup.exe" | Select-Object -First 1
$Msi = Get-ChildItem -LiteralPath (Join-Path $BundleRoot "msi") -Filter "*_${Version}_*.msi" | Select-Object -First 1
if (-not $Setup) { throw "No $Version installer found in $BundleRoot\nsis." }
foreach ($file in @($Setup, $Msi)) {
    if ($file -and -not (Test-Path -LiteralPath "$($file.FullName).sig")) {
        throw "$($file.Name) is not signed (no .sig next to it). Was the build run by this script?"
    }
}

$OutDir = Join-Path $RepoRoot "release\$Tag"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$assets = @()
foreach ($file in @($Setup, $Msi)) {
    if (-not $file) { continue }
    Copy-Item -LiteralPath $file.FullName -Destination $OutDir -Force
    Copy-Item -LiteralPath "$($file.FullName).sig" -Destination $OutDir -Force
    $assets += (Join-Path $OutDir $file.Name)
}

$download = "https://github.com/$Repo/releases/download/$Tag"
$setupSig = (Get-Content -LiteralPath "$($Setup.FullName).sig" -Raw).Trim()
$platforms = [ordered]@{
    "windows-x86_64" = [ordered]@{ signature = $setupSig; url = "$download/$($Setup.Name)" }
    "windows-x86_64-nsis" = [ordered]@{ signature = $setupSig; url = "$download/$($Setup.Name)" }
}
if ($Msi) {
    $platforms["windows-x86_64-msi"] = [ordered]@{
        signature = (Get-Content -LiteralPath "$($Msi.FullName).sig" -Raw).Trim()
        url = "$download/$($Msi.Name)"
    }
}
# First paragraph of the notes is what the app shows next to "Update to ...".
$notes = (Get-Content -LiteralPath $NotesPath -Raw) -replace "`r", ""
$summary = (($notes -split "`n`n") | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith("#") } | Select-Object -First 1)
$latest = [ordered]@{
    version = $Version
    notes = if ($summary) { $summary.Trim() } else { "EchoSpeak $Version" }
    pub_date = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    platforms = $platforms
}
$LatestPath = Join-Path $OutDir "latest.json"
[System.IO.File]::WriteAllText($LatestPath, ($latest | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
$assets += $LatestPath

Write-Host ""
Write-Host "Release files for $Tag are in $OutDir"
$assets | ForEach-Object { Write-Host "  $_" }

if (-not $Publish) {
    Write-Host ""
    Write-Host "Nothing was published. To publish, run this again with -SkipBuild -Publish."
    exit 0
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "The GitHub CLI (gh) is not installed: https://cli.github.com" }
$commit = (git -C $RepoRoot rev-parse HEAD).Trim()
$pushed = git -C $RepoRoot branch -r --contains $commit
if (-not $pushed) { throw "Commit $commit is not on GitHub yet. Push your branch first, then run with -SkipBuild -Publish." }
gh release create $Tag @assets --repo $Repo --target $commit --title "EchoSpeak $Version" --notes-file $NotesPath --latest
if ($LASTEXITCODE -ne 0) { throw "gh release create failed." }
Write-Host "Published https://github.com/$Repo/releases/tag/$Tag"
