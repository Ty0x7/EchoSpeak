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

!macro NSIS_HOOK_POSTINSTALL
  !insertmacro EchoSpeakRepairShortcut "$DESKTOP\EchoSpeak.lnk"
  !insertmacro EchoSpeakRepairShortcut "$SMPROGRAMS\EchoSpeak.lnk"
  !insertmacro EchoSpeakRepairShortcut "$SMPROGRAMS\EchoSpeak\EchoSpeak.lnk"
  !insertmacro EchoSpeakRepairShortcut "$APPDATA\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\EchoSpeak.lnk"
!macroend
