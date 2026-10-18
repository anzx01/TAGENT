!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToLog 'schtasks.exe /Delete /TN "TAGENT Scheduled Backend" /F'
!macroend
