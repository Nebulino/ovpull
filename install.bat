@echo off
REM Double-click entry point for install.ps1 (Windows).
REM Puts `ovpull` on your PATH; the window stays open so you can read the output.
setlocal
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
if /I not "%OVPULL_NO_PAUSE%"=="1" pause
endlocal
