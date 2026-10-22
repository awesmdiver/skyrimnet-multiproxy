<#
.SYNOPSIS
    Builds and runs the Address Library check test (plain cl.exe via vcvars, no game needed).

.DESCRIPTION
    Compiles the REAL ../../src/address_library_check.h, so it tests the source the mod ships.
    Edit $vcvars if your Visual Studio lives somewhere else.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$vcvars = "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) {
    Write-Host "Couldn't find vcvars64.bat at the expected path -- edit `$vcvars in this script." -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force -Path bin | Out-Null

cmd /c "`"$vcvars`" && cl /nologo /EHsc /std:c++20 /utf-8 /W4 /WX /I `"..\..\src`" test_address_library.cpp /Fo:bin\ /Fe:bin\test_address_library.exe"
if ($LASTEXITCODE -ne 0) { Write-Host "Build failed" -ForegroundColor Red; exit 1 }

& .\bin\test_address_library.exe
exit $LASTEXITCODE
