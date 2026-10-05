param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [string]$InstalledPath = "",
    [string]$ModelsPath = "",
    [string]$OutputPath = ""
)
$ErrorActionPreference = "Stop"
function Measure-Directory([string]$Path) {
    if (-not $Path) { return $null }
    $root = (Resolve-Path -LiteralPath $Path).Path
    $files = @(Get-ChildItem -LiteralPath $root -File -Recurse -Force | Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) })
    return [ordered]@{
        path = $root
        bytes = [long](($files | Measure-Object -Property Length -Sum).Sum)
        largest = @($files | Sort-Object Length -Descending | Select-Object -First 20 | ForEach-Object { @{ path = $_.FullName; bytes = $_.Length } })
    }
}
$installer = Get-Item -LiteralPath (Resolve-Path -LiteralPath $InstallerPath).Path
$report = [ordered]@{
    measured_at = (Get-Date).ToUniversalTime().ToString("o")
    installer = @{ path = $installer.FullName; bytes = $installer.Length; sha256 = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash }
    installed_app = (Measure-Directory $InstalledPath)
    optional_models = (Measure-Directory $ModelsPath)
}
$json = $report | ConvertTo-Json -Depth 6
if ($OutputPath) { [IO.File]::WriteAllText($OutputPath, $json, [Text.UTF8Encoding]::new($false)) }
$json
