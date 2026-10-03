<#
.SYNOPSIS
    Builds the job-object test tools into ./bin/ (pure Win32, plain cl.exe via vcvars).

.DESCRIPTION
    job_probe.exe, job_box.exe and exit_probe_dll.dll all compile the REAL
    ../../src/multiproxy_channel.cpp, so they always test the source the mod ships, not a copy.
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

$common = "/nologo /EHsc /std:c++20 /utf-8 /W4 /I `"..\..\src`""
$libs = "ws2_32.lib ole32.lib oleaut32.lib uuid.lib shell32.lib"
$channel = "..\..\src\multiproxy_channel.cpp"

function Build-One($label, $cmdTail) {
    Write-Host "Building $label..." -ForegroundColor Cyan
    cmd /c "`"$vcvars`" && cl $common $cmdTail"
    if ($LASTEXITCODE -ne 0) { Write-Host "Build failed for $label" -ForegroundColor Red; exit 1 }
}

Build-One "job_probe"  "job_probe.cpp $channel /Fo:bin\ /Fe:bin\job_probe.exe $libs"
Build-One "job_box"    "job_box.cpp $channel /Fo:bin\ /Fe:bin\job_box.exe $libs"
Build-One "exit_probe" "exit_probe.cpp /Fo:bin\ /Fe:bin\exit_probe.exe ws2_32.lib"
Build-One "exit_probe_dll" "/LD exit_probe_dll.cpp $channel /Fo:bin\ /Fe:bin\exit_probe_dll.dll $libs"
Write-Host "Built into $PSScriptRoot\bin" -ForegroundColor Green
