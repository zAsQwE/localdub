Unicode true
Name "LocalDub"
OutFile "${OUTPUT}"
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
SetCompressor /SOLID lzma
SetCompressorDictSize 32
!include "FileFunc.nsh"
Var args
Var extract

Section
  ${GetParameters} $args
  ClearErrors
  ${GetOptions} $args "/EXTRACT=" $extract
  IfErrors normal
    StrCmp $extract "" normal
    StrCpy $INSTDIR $extract
    Goto unpack
  normal:
    StrCpy $INSTDIR "$LOCALAPPDATA\LocalDub\app\${BUILD_ID}"
    IfFileExists "$INSTDIR\.ready" launch
  unpack:
    ClearErrors
    SetOutPath "$INSTDIR"
    File /r "${PAYLOAD}\*"
    IfErrors failed
    FileOpen $0 "$INSTDIR\.ready" w
    FileWrite $0 "${BUILD_ID}"
    FileClose $0
    StrCmp $extract "" launch done
  launch:
    ClearErrors
    SetOutPath "$INSTDIR"
    Exec '"$INSTDIR\runtime\python\pythonw.exe" -X utf8 "$INSTDIR\boot.py" $args'
    IfErrors failed done
  failed:
    MessageBox MB_OK|MB_ICONSTOP "LocalDub could not unpack or start. Try moving the program to a writable folder."
    SetErrorLevel 1
  done:
SectionEnd
