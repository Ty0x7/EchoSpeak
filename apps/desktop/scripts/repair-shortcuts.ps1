param([Parameter(Mandatory)][string]$ExecutablePath, [string[]]$ShortcutDirectories = @())
$ErrorActionPreference = 'Stop'
$currentExe = (Resolve-Path -LiteralPath $ExecutablePath).Path
$product = [Diagnostics.FileVersionInfo]::GetVersionInfo($currentExe).ProductName
$currentVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($currentExe)
if ($product -ne 'EchoSpeak') { throw 'Shortcut repair requires the installed EchoSpeak executable.' }
if (-not $ShortcutDirectories.Count) {
    $ShortcutDirectories = @(
        [Environment]::GetFolderPath('Desktop'),
        [Environment]::GetFolderPath('CommonDesktopDirectory'),
        [Environment]::GetFolderPath('Programs'),
        [Environment]::GetFolderPath('CommonPrograms'),
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'EchoSpeak'),
        (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar')
    )
}
$shell = New-Object -ComObject WScript.Shell
if (-not ('EchoSpeakShortcutNotify' -as [type])) {
    Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class EchoSpeakShortcutNotify {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    public static extern void SHChangeNotify(uint eventId, uint flags, string item1, string item2);
}
'@
}
foreach ($directory in $ShortcutDirectories | Select-Object -Unique) {
    if (-not $directory -or -not (Test-Path -LiteralPath $directory)) { continue }
    foreach ($file in Get-ChildItem -LiteralPath $directory -Filter '*.lnk' -File) {
        try {
            $link = $shell.CreateShortcut($file.FullName)
            $target = [Environment]::ExpandEnvironmentVariables($link.TargetPath)
            if ([IO.Path]::GetFileName($target) -notin @('echospeak.exe', 'echospeak-desktop.exe')) { continue }
            $isEcho = $file.BaseName -match '^EchoSpeak(?:\s|$)'
            if (Test-Path -LiteralPath $target) {
                $targetInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($target)
                $isEcho = $targetInfo.ProductName -eq 'EchoSpeak'
                # Launching an older copy must never repoint newer shortcuts backwards.
                $targetVersion = [Version]::new($targetInfo.FileMajorPart, $targetInfo.FileMinorPart, $targetInfo.FileBuildPart, $targetInfo.FilePrivatePart)
                $newVersion = [Version]::new($currentVersion.FileMajorPart, $currentVersion.FileMinorPart, $currentVersion.FileBuildPart, $currentVersion.FilePrivatePart)
                if ($targetVersion -gt $newVersion) { continue }
            }
            if (-not $isEcho -or $target -eq $currentExe) { continue }
            # Repair existing links, including pins; preserve arguments and pin identity.
            $link.TargetPath = $currentExe
            $link.WorkingDirectory = Split-Path -Parent $currentExe
            $link.IconLocation = "$currentExe,0"
            $link.Save()
            [EchoSpeakShortcutNotify]::SHChangeNotify(0x00002000, 0x0005, $file.FullName, $null)
        } catch { Write-Warning "Could not repair $($file.Name): $($_.Exception.Message)" }
    }
}
