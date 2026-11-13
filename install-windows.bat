@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows.ps1" -Action Install %*
set "LOCALDUB_EXIT=%errorlevel%"
pause
exit /b %LOCALDUB_EXIT%
