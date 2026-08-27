@echo off
setlocal EnableExtensions
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-local.ps1"
set "STOP_EXIT=%ERRORLEVEL%"

if not "%STOP_EXIT%"=="0" (
  echo [WARNING] Services could not be stopped completely. Try Run as administrator.
) else (
  echo Done. seeed-arm-control stopped.
)
pause
exit /b %STOP_EXIT%
