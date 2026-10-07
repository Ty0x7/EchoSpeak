; Use Tauri's native ShellLink helpers during installation only. Repair standard
; links with known installation targets, keeping arguments and existing pins.
!macro EchoSpeakRepairShortcut shortcut
  ${If} ${FileExists} "${shortcut}"
    !insertmacro IsShortcutTarget "${shortcut}" "$INSTDIR\echospeak.exe"
    Pop $4
    !insertmacro IsShortcutTarget "${shortcut}" "$LOCALAPPDATA\EchoSpeak\echospeak.exe"
    Pop $5
    !insertmacro IsShortcutTarget "${shortcut}" "$LOCALAPPDATA\EchoSpeak\echospeak-desktop.exe"
    Pop $6
    ${If} $4 = 1
    ${OrIf} $5 = 1
    ${OrIf} $6 = 1
      !insertmacro SetShortcutTarget "${shortcut}" "$INSTDIR\${MAINBINARYNAME}.exe"
      !insertmacro SetLnkAppUserModelId "${shortcut}"
      System::Call 'shell32::SHChangeNotify(i 0x00002000, i 0x0005, w "${shortcut}", p 0)'
    ${EndIf}
  ${EndIf}
!macroend

; Start every install with a clean backend folder. The installer only adds and
; overwrites files, so modules left by an older version stayed behind and could
; break the new one (11.1.0: a stale backports/zstd extension made langchain's
; imports fail). Your data is elsewhere (%LOCALAPPDATA%\ai.echospeak.desktop).
!macro NSIS_HOOK_PREINSTALL
  ${If} $INSTDIR != ""
  ${AndIf} ${FileExists} "$INSTDIR\backend\*.*"
    RMDir /r "$INSTDIR\backend"
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  !insertmacro EchoSpeakRepairShortcut "$DESKTOP\EchoSpeak.lnk"
  !insertmacro EchoSpeakRepairShortcut "$SMPROGRAMS\EchoSpeak.lnk"
  !insertmacro EchoSpeakRepairShortcut "$SMPROGRAMS\EchoSpeak\EchoSpeak.lnk"
  !insertmacro EchoSpeakRepairShortcut "$APPDATA\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\EchoSpeak.lnk"
!macroend
