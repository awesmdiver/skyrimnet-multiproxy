<#
.SYNOPSIS
    Builds and runs the plugin tests (plain cl.exe via vcvars, no game needed).

.DESCRIPTION
    Compiles the REAL ../../src/plugin_support.h and skse_plugin_api.h, so it tests the source the
    mod ships. If the Release DLL is built (build\Release\SkyrimNetMultiProxy.dll), the test also
    loads it and checks the SKSE entry points and flags, and dumpbin must list the three exports.
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

cmd /c "`"$vcvars`" && cl /nologo /EHsc /std:c++20 /utf-8 /W4 /WX /I `"..\..\src`" test_plugin_support.cpp /Fo:bin\ /Fe:bin\test_plugin_support.exe /link ws2_32.lib"
if ($LASTEXITCODE -ne 0) { Write-Host "Build failed" -ForegroundColor Red; exit 1 }

$dll = Join-Path $PSScriptRoot "..\..\build\Release\SkyrimNetMultiProxy.dll"
if (Test-Path $dll) {
    $dll = (Resolve-Path $dll).Path
    & .\bin\test_plugin_support.exe $dll
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $exports = cmd /c "`"$vcvars`" >nul && dumpbin /exports `"$dll`""
    foreach ($name in "SKSEPlugin_Version", "SKSEPlugin_Query", "SKSEPlugin_Load") {
        if (-not ($exports -match "\b$name\b")) { Write-Host "FAIL: export $name missing" -ForegroundColor Red; exit 1 }
    }
    Write-Host "exports: SKSEPlugin_Version, SKSEPlugin_Query, SKSEPlugin_Load present"

    $bytes = [IO.File]::ReadAllBytes($dll)
    $text = [Text.Encoding]::ASCII.GetString($bytes) + [Text.Encoding]::Unicode.GetString($bytes)
    foreach ($bad in "beta", "CommonLibSSE", "versionlib-", "version-1-") {
        if ($text -match "(?i)$bad") { Write-Host "FAIL: the DLL contains '$bad'" -ForegroundColor Red; exit 1 }
    }
    Write-Host "DLL has no 'beta', CommonLibSSE or Address Library file names"
} else {
    & .\bin\test_plugin_support.exe
    exit $LASTEXITCODE
}
