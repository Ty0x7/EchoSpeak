param(
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$DesktopRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SpecPath = Join-Path $DesktopRoot "backend\echospeak_backend.spec"
$TauriRoot = Join-Path $DesktopRoot "src-tauri"
$BuildRoot = Join-Path $DesktopRoot ".build\sidecar"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"

$PyInstallerVersion = (& $PythonExecutable -m PyInstaller --version).Trim()
if ($LASTEXITCODE -ne 0 -or $PyInstallerVersion -ne "6.21.0") {
    throw "PyInstaller 6.21.0 is required; found '$PyInstallerVersion'."
}

New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot | Out-Null
& $PythonExecutable -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    $SpecPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

# One-folder build: stage the whole folder as a Tauri resource (bundle.resources).
$SourceFolder = Join-Path $DistRoot "echospeak-backend"
$SourceBinary = Join-Path $SourceFolder "echospeak-backend.exe"
if (-not (Test-Path -LiteralPath $SourceBinary)) {
    throw "Expected backend was not produced at $SourceBinary."
}
# Fail the build if the bundle is incomplete (e.g. a module failed to compile
# during analysis and PyInstaller silently left it out).
$env:ECHOSPEAK_DATA_DIR = Join-Path $BuildRoot "selfcheck-data"
$env:ECHOSPEAK_TESTING = "1"
& $SourceBinary --self-check
$SelfCheckExit = $LASTEXITCODE
Remove-Item Env:ECHOSPEAK_TESTING -ErrorAction SilentlyContinue
Remove-Item Env:ECHOSPEAK_DATA_DIR -ErrorAction SilentlyContinue
if ($SelfCheckExit -ne 0) {
    throw "Packaged backend failed its self-check (exit $SelfCheckExit). Fix the import error above and rebuild."
}
$StagedFolder = Join-Path $TauriRoot "backend-dist"
if (Test-Path -LiteralPath $StagedFolder) {
    Remove-Item -LiteralPath $StagedFolder -Recurse -Force
}
Copy-Item -LiteralPath $SourceFolder -Destination $StagedFolder -Recurse
Write-Host "Backend ready: $StagedFolder"
