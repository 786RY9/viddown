# Forwards to dl.ps1 so `.\viddown` works in PowerShell.
& (Join-Path $PSScriptRoot "dl.ps1") @args
exit $LASTEXITCODE
