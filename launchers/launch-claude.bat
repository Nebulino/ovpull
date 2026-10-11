@echo off
REM launch-claude.bat - double-click entry point for launch-claude.ps1
REM
REM   double-click          -> Claude session for this project (reuses the last one)
REM   launch-claude.bat -New      -> fresh conversation
REM   launch-claude.bat -DryRun   -> show what would run, launch nothing
REM
REM The execution policy is bypassed for this single call only; nothing about
REM the machine is changed. The window stays open after the session ends so you
REM can read any error message.
setlocal
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-claude.ps1" %*
echo.
if /I not "%CLAUDE_LAUNCHER_HOLD%"=="0" pause
endlocal
