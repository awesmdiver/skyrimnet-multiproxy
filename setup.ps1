<#
.SYNOPSIS
    One-time setup for SkyrimNet MultiProxy: makes sure Python is installed
    (installing it automatically if not), installs the proxy's Python packages, and
    prints the remaining manual steps.

.DESCRIPTION
    Run this once after unzipping the release. It does NOT copy files into your Skyrim
    install or edit SkyrimNetMultiProxy.ini for you -- those need your own game path, so this
    prints clear instructions for that last step instead of guessing at it.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Python {
    foreach ($candidate in @("python", "py")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            try {
                $verOutput = & $candidate --version 2>&1
                if ($verOutput -match "Python (\d+)\.(\d+)") {
                    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                    if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
                        return @{ Exe = $candidate; Version = $verOutput; Source = $found.Source }
                    }
                }
            } catch { }
        }
    }
    return $null
}

Write-Host "=== SkyrimNet MultiProxy -- Setup ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. Find (or install) Python 3.10+ -----------------------------------------
Write-Host "[1/2] Checking for Python 3.10+..." -ForegroundColor Yellow

$python = Find-Python
if ($python) {
    Write-Host "  Found $($python.Version) ($($python.Source))" -ForegroundColor Green
}

if (-not $python) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "  Python wasn't found -- installing it automatically via winget..." -ForegroundColor Yellow
        Write-Host "  (this only happens once; it may take a minute)" -ForegroundColor Yellow
        winget install --id Python.Python.3.14 -e --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) {
            # Refresh PATH in this process so the newly-installed python.exe can be found
            # without having to close and reopen this window.
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("Path", "User")
            $python = Find-Python
        }
    }
}

if (-not $python) {
    Write-Host ""
    Write-Host "  Couldn't install Python automatically." -ForegroundColor Red
    Write-Host "  Please install it yourself from https://www.python.org/downloads/ -- check the box" -ForegroundColor Red
    Write-Host "  that says ""Add python.exe to PATH"" during install, then run this script again." -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

$pythonExe = $python.Exe

# --- 2. Install the proxy's Python packages ------------------------------------
Write-Host ""
Write-Host "[2/2] Installing the packages the proxy needs..." -ForegroundColor Yellow
& $pythonExe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  Something went wrong installing the packages -- see the errors above." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

# --- Done -----------------------------------------------------------------------
Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " Setup complete!" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Two manual steps left (these need your own Skyrim folder, so this script" -ForegroundColor Yellow
Write-Host "can't do them for you):" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Copy SkyrimNetMultiProxy.dll and SkyrimNetMultiProxy.ini into:" -ForegroundColor White
Write-Host "       <your Skyrim folder>\Data\SKSE\Plugins\" -ForegroundColor White
Write-Host "     (your mod manager can do this for you instead, if you prefer)" -ForegroundColor White
Write-Host ""
Write-Host "  2. Open that copy of SkyrimNetMultiProxy.ini in a text editor and set:" -ForegroundColor White
Write-Host "       PythonExe   -> $((Get-Command $pythonExe).Source)" -ForegroundColor White
Write-Host "       ProxyScript -> $PSScriptRoot\proxy.py" -ForegroundColor White
Write-Host "       WorkDir     -> $PSScriptRoot" -ForegroundColor White
Write-Host ""
Write-Host "Then launch Skyrim as normal -- the proxy starts itself." -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to close"
