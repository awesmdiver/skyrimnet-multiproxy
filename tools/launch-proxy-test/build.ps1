<#
.SYNOPSIS
    Builds the LaunchProxy() test harness + diagnostic tools into ./bin/.

.DESCRIPTION
    All of this is pure Win32 (no CommonLibSSE, no SKSE), so it compiles standalone with plain
    cl.exe via vcvars -- no CMake project needed. test_launch.cpp links directly against the
    REAL ../../src/proxy_launcher.cpp, so it's always testing the actual current source, not a
    copy of it.

    Requires a Visual Studio install with the C++ toolchain (same one the real plugin builds
    with). Edit $vcvars below if yours lives somewhere else.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$vcvars = "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) {
    Write-Host "Couldn't find vcvars64.bat at the expected path -- edit `$vcvars in this script." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path bin | Out-Null

function Build-One($name, $extraSources, $extraLibs) {
    Write-Host "Building $name..." -ForegroundColor Cyan
    $cmd = "`"$vcvars`" && cl /nologo /EHsc /std:c++20 /I `"..\..\src`" $name.cpp $extraSources $extraLibs /Fo:bin\ /Fe:bin\$name.exe"
    cmd /c $cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Build failed for $name" -ForegroundColor Red
        exit 1
    }
}

Build-One "instant_exit" "" ""
Build-One "test_launch" "..\..\src\proxy_launcher.cpp" "ws2_32.lib"
Build-One "timing_check" "" "ws2_32.lib"

Write-Host ""
Write-Host "Built: bin\instant_exit.exe, bin\test_launch.exe, bin\timing_check.exe" -ForegroundColor Green
Write-Host "See README.md for how to run the two test_launch scenarios." -ForegroundColor Green
