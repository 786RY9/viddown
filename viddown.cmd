@echo off
REM Local 'viddown' command; --install copies a similar shim onto PATH.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dl.ps1" %*
exit /b %ERRORLEVEL%
