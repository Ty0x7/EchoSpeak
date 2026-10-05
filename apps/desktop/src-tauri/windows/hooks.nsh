!macro NSIS_HOOK_POSTINSTALL
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "$INSTDIR\repair-shortcuts.ps1" -ExecutablePath "$INSTDIR\${MAINBINARYNAME}.exe"'
!macroend
