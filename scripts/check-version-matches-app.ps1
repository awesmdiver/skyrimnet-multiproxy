<#
.SYNOPSIS
    Fails when the mod's version (CMake project version) differs from the MultiProxy app's version.

.DESCRIPTION
    The app's version lives in the dev project: PROXY_VERSION in multiproxy.py, repeated in endpoint.json.
    Both must agree with each other and with CMakeLists.txt's project(... VERSION X.Y.Z ...).
    ModPacer compares the mod's version with the latest release, so the two must never drift.
    Run as the first step of build-mod-package.ps1 and build-release.ps1.

.PARAMETER DevRoot
    The dev project folder. Default: the first skyrimnet-multiproxy-dev folder found next to this project
    (or next to the main project, when this is a terminal worktree under terminals\).

.PARAMETER AppVersion
    Use this version instead of the dev project's. Only for the window between "release started" and
    "app version bumped in the dev project"; the script says so out loud when it is used.
#>
param([string]$DevRoot, [string]$AppVersion)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

$cmake = [regex]::Match((Get-Content (Join-Path $root "CMakeLists.txt") -Raw), '(?s)project\s*\(.*?VERSION\s+(\d+\.\d+\.\d+)')
if (-not $cmake.Success) { Write-Host "FAIL: no project VERSION X.Y.Z in CMakeLists.txt" -ForegroundColor Red; exit 1 }
$modVersion = $cmake.Groups[1].Value

if ($AppVersion) {
    Write-Host "NOTE: using -AppVersion $AppVersion instead of reading the dev project." -ForegroundColor Yellow
} else {
    if (-not $DevRoot) {
        $dir = $root
        while ($dir -and -not $DevRoot) {
            $try = Join-Path (Split-Path $dir -Parent) "skyrimnet-multiproxy-dev"
            if (Test-Path (Join-Path $try "multiproxy.py")) { $DevRoot = $try }
            $dir = Split-Path $dir -Parent
        }
    }
    if (-not $DevRoot -or -not (Test-Path (Join-Path $DevRoot "multiproxy.py"))) {
        Write-Host "FAIL: can't find the skyrimnet-multiproxy-dev project (pass -DevRoot)." -ForegroundColor Red; exit 1
    }
    $py = [regex]::Match((Get-Content (Join-Path $DevRoot "multiproxy.py") -Raw), '(?m)^PROXY_VERSION\s*=\s*"(\d+\.\d+\.\d+)"')
    if (-not $py.Success) { Write-Host "FAIL: no PROXY_VERSION = ""X.Y.Z"" line in multiproxy.py" -ForegroundColor Red; exit 1 }
    $AppVersion = $py.Groups[1].Value
    $endpoint = Join-Path $DevRoot "endpoint.json"
    if (Test-Path $endpoint) {
        $ev = (Get-Content $endpoint -Raw | ConvertFrom-Json).version
        if ($ev -ne $AppVersion) {
            Write-Host "FAIL: the app disagrees with itself: multiproxy.py says $AppVersion, endpoint.json says $ev." -ForegroundColor Red; exit 1
        }
    }
}

if ($modVersion -ne $AppVersion) {
    Write-Host "FAIL: the mod is $modVersion (CMakeLists.txt) but the app is $AppVersion. They must match." -ForegroundColor Red
    exit 1
}
Write-Host "version check: mod and app are both $modVersion" -ForegroundColor Green
exit 0
