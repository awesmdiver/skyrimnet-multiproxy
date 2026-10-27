<#
.SYNOPSIS
    Builds the Release DLL and the versioned mod package SkyrimNetMultiProxy-Mod-X.Y.Z.zip.

.DESCRIPTION
    1. scripts\check-version-matches-app.ps1 (mod version must equal the app's version).
    2. cmake configure + Release build.
    3. Checks the DLL: SKSE export reports the version (tools\plugin-test), no CommonLibSSE / beta strings,
       and the startup log string "SkyrimNetMultiProxy vX.Y.Z" is inside the DLL.
    4. Zips SKSE\Plugins\SkyrimNetMultiProxy.dll into <OutDir>\SkyrimNetMultiProxy-Mod-X.Y.Z.zip and prints its SHA-256.
    Never publishes anything.
#>
param([Parameter(Mandatory)][string]$OutDir, [string]$AppVersion)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$checkArgs = @{}
if ($AppVersion) { $checkArgs.AppVersion = $AppVersion }
& (Join-Path $root "scripts\check-version-matches-app.ps1") @checkArgs
if ($LASTEXITCODE -ne 0) { exit 1 }

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    $vsCmake = "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
    if (-not (Test-Path $vsCmake)) { throw "cmake not found -- edit the Visual Studio path in this script." }
    $env:PATH = "$vsCmake;$env:PATH"
}
$version = [regex]::Match((Get-Content CMakeLists.txt -Raw), '(?s)project\s*\(.*?VERSION\s+(\d+\.\d+\.\d+)').Groups[1].Value
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DCMAKE_SKIP_INSTALL_RULES=ON
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }
cmake --build build --config Release
if ($LASTEXITCODE -ne 0) { throw "build failed" }

$dll = Join-Path $root "build\Release\SkyrimNetMultiProxy.dll"
$bytes = [IO.File]::ReadAllBytes($dll)
$text = [Text.Encoding]::ASCII.GetString($bytes)
if ($text -notmatch [regex]::Escape("SkyrimNetMultiProxy v$version by awesmdiver")) { throw "The DLL's startup log line doesn't say v$version" }
Write-Host "startup log line says v$version"
foreach ($bad in "beta", "CommonLibSSE", "versionlib-") {
    if (($text + [Text.Encoding]::Unicode.GetString($bytes)) -match "(?i)$bad") { throw "The DLL contains '$bad'" }
}

# Loads the DLL and checks the SKSE entry points and that the exported version equals $version.
Push-Location (Join-Path $root "tools\plugin-test")
try {
    .\build.ps1
    if ($LASTEXITCODE -ne 0) { throw "plugin tests failed" }
    .\bin\test_plugin_support.exe $dll $version
    if ($LASTEXITCODE -ne 0) { throw "the DLL's SKSE export doesn't report $version" }
} finally { Pop-Location }

$stage = Join-Path $env:TEMP "mod-package-stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $stage "SKSE\Plugins") -Force | Out-Null
Copy-Item $dll (Join-Path $stage "SKSE\Plugins\SkyrimNetMultiProxy.dll")
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$zip = Join-Path $OutDir "SkyrimNetMultiProxy-Mod-$version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage "SKSE") -DestinationPath $zip -CompressionLevel Optimal
Write-Host "Built $zip"
Write-Host ("SHA-256: " + (Get-FileHash $zip -Algorithm SHA256).Hash)
