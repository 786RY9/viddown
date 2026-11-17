# viddown - Windows PowerShell wrapper for the viddown Docker container
#
# Usage:
#   viddown                         Open interactive menu (prompts for URL)
#   viddown URL                     Open interactive menu with URL pre-filled
#   viddown URL --audio-only        Download audio only (best MP3)
#   viddown URL -o DIR              Save to a custom directory
#   viddown --build / --rebuild     (Re)build the Docker image
#   viddown --install               Add the 'viddown' command to your PATH
#   viddown --uninstall             Remove the global 'viddown' command
#   viddown -h / --help             Show this help
#
# Downloaded files go to the current directory (or -o DIR).

$ErrorActionPreference = "Stop"
$ImageName = "viddown"
$ScriptDir = $PSScriptRoot
$OutputDir = (Get-Location).ProviderPath
$Url = $null
$ExtraArgs = New-Object System.Collections.Generic.List[string]
$InstallDir = Join-Path $env:LOCALAPPDATA "viddown"

function Show-Usage {
    Write-Host ""
    Write-Host "  viddown - download from YouTube, Instagram, TikTok and more"
    Write-Host ""
    Write-Host "  Usage:"
    Write-Host "    viddown                         Interactive menu (prompts for URL)"
    Write-Host "    viddown URL                     Interactive menu with URL pre-filled"
    Write-Host "    viddown URL --audio-only        Download audio only (best MP3)"
    Write-Host "    viddown URL -o DIR              Save to a custom directory"
    Write-Host "    viddown --build / --rebuild     (Re)build the Docker image"
    Write-Host "    viddown --install               Make 'viddown' work from any folder"
    Write-Host "    viddown --uninstall             Remove the global 'viddown' command"
    Write-Host "    viddown -h / --help             Show this help"
    Write-Host ""
    Write-Host "  Downloaded files go to: current directory (or specify with -o DIR)"
    Write-Host "  The Docker image is built automatically on first run."
    Write-Host ""
}

function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "  Docker was not found on PATH. Install Docker Desktop and leave it running." -ForegroundColor Red
        exit 1
    }
}

function Test-ImageExists {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker image inspect $ImageName 2>$null | Out-Null
    $exists = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $exists
}

function Build-Image {
    Assert-Docker
    Write-Host "Building Docker image '$ImageName'..."
    docker build -t $ImageName $ScriptDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Docker build failed (exit $LASTEXITCODE)." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "Image built successfully."
}

function Install-Viddown {
    $launcher = Join-Path $ScriptDir "dl.ps1"
    if (-not (Test-Path -LiteralPath $launcher)) {
        Write-Host "  Could not find dl.ps1 at $launcher" -ForegroundColor Red
        exit 1
    }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $shimPath = Join-Path $InstallDir "viddown.cmd"
    $lines = @(
        "@echo off",
        "powershell -NoProfile -ExecutionPolicy Bypass -File `"$launcher`" %*",
        "exit /b %ERRORLEVEL%"
    )
    Set-Content -LiteralPath $shimPath -Value $lines -Encoding ASCII

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $userPath) { $userPath = "" }
    $parts = @($userPath.Split(';') | Where-Object { $_ -ne "" })
    if ($parts -notcontains $InstallDir) {
        if ($userPath.Trim() -eq "") {
            $newPath = $InstallDir
        } else {
            $newPath = $userPath.TrimEnd(';') + ";" + $InstallDir
        }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    }
    if (($env:Path -split ';' | Where-Object { $_ -eq $InstallDir }).Count -eq 0) {
        $env:Path = $InstallDir + ";" + $env:Path
    }

    Write-Host ""
    Write-Host "  Installed global command: viddown" -ForegroundColor Green
    Write-Host "  Shim: $shimPath"
    Write-Host ""
    Write-Host "  Open a NEW PowerShell or Command Prompt window, then run:"
    Write-Host '    viddown "https://www.youtube.com/watch?v=VIDEO_ID"'
    Write-Host "    viddown --help"
    Write-Host ""
    Write-Host "  Files save in whichever folder you are in when you run it."
    Write-Host ""
}

function Uninstall-Viddown {
    $shimPath = Join-Path $InstallDir "viddown.cmd"
    if (Test-Path -LiteralPath $shimPath) {
        Remove-Item -LiteralPath $shimPath -Force
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath) {
        $parts = @($userPath.Split(';') | Where-Object { $_ -ne "" -and $_ -ne $InstallDir })
        [Environment]::SetEnvironmentVariable("Path", ($parts -join ';'), "User")
    }
    Write-Host ""
    Write-Host "  Removed the global viddown command." -ForegroundColor Green
    Write-Host "  Open a new terminal for PATH changes to take effect."
    Write-Host ""
}

$i = 0
while ($i -lt $args.Count) {
    $a = [string]$args[$i]
    if ($a -in @("--build", "--rebuild")) {
        Build-Image
        exit 0
    }
    if ($a -in @("--install")) {
        Install-Viddown
        exit 0
    }
    if ($a -in @("--uninstall")) {
        Uninstall-Viddown
        exit 0
    }
    if ($a -in @("-h", "--help")) {
        Show-Usage
        exit 0
    }
    if ($a -in @("-o", "--output")) {
        if ($i + 1 -ge $args.Count) {
            Write-Host "  -o / --output requires a directory path." -ForegroundColor Red
            exit 1
        }
        $OutputDir = [string]$args[$i + 1]
        $i += 2
        continue
    }
    if ($a -in @("--audio-only", "--update")) {
        $ExtraArgs.Add($a)
        $i++
        continue
    }
    if ($null -eq $Url) {
        $Url = $a
    } else {
        $ExtraArgs.Add($a)
    }
    $i++
}

Assert-Docker

if (-not (Test-ImageExists)) {
    Write-Host "Docker image '$ImageName' not found. Building now..."
    Build-Image
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).ProviderPath

$dockerArgs = New-Object System.Collections.Generic.List[string]
$dockerArgs.Add("run")
if (-not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) {
    $dockerArgs.Add("-it")
}
$dockerArgs.Add("--rm")
$dockerArgs.Add("-v")
$dockerArgs.Add(($OutputDir + ':/downloads'))
$dockerArgs.Add($ImageName)
if ($Url) {
    $dockerArgs.Add($Url)
}
$dockerArgs.Add("-o")
$dockerArgs.Add("/downloads")
foreach ($extra in $ExtraArgs) {
    $dockerArgs.Add($extra)
}

& docker @dockerArgs
exit $LASTEXITCODE
