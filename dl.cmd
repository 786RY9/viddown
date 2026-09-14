@echo off
REM Forwards to dl.ps1 so `dl` works from Command Prompt.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dl.ps1" %*
exit /b %ERRORLEVEL%
