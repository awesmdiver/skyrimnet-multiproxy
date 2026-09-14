# Packages the downloadable release zip (SkyrimNetMultiProxy-vX.Y.Z.zip). Unlike
# vortex-collection-tools' own build-release.ps1, this repo is NOT the source of truth for every
# release file -- proxy.py, requirements.txt, config.example.json, proxy.ini.example, and
# start-proxy.bat all come from the private skyrimnet-multiproxy-dev repo at release-build time
# (see the v2.0.0 commit message), so this script can't regenerate them itself. It expects them
# already staged in release-staging\ (gitignored) -- copy the current versions there by hand before
# running this (or reuse the ones already in a prior release zip if nothing proxy-side changed).
# release-staging\ also holds LICENSE-proxy.txt, a copy of the dev repo's own LICENSE file -- the
# bundled proxy.py carries MIT-licensed code from galanx (Claude-SkyrimNet-Proxy) and rhinos0608
# (skyrimnet-codex-proxy), and MIT requires their copyright notices to travel with the release.
#
# What this script DOES own: assembling the SKSE plugin itself (SkyrimNetMultiProxy.dll +
# SkyrimNetMultiProxy.ini) into its own importable archive, SkyrimNetMultiProxy.zip, so a mod
# manager (Vortex/MO2) can install it like any other mod instead of the user copying two loose
# files into Data\SKSE\Plugins\ by hand. That inner zip's internal path is SKSE\Plugins\... at its
# root -- mod managers deploy an archive's own root relative to the game's Data\ folder, so this is
# what makes SKSE\Plugins\SkyrimNetMultiProxy.dll land in the right place automatically.
#
# Usage:
#   .\build-release.ps1
#
# Requires: SkyrimNetMultiProxy.dll already built (build\Release\SkyrimNetMultiProxy.dll -- via
# CMake/Visual Studio), and release-staging\ populated. Reads the version from skse-project.json.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$version = (Get-Content (Join-Path $root "skse-project.json") -Raw | ConvertFrom-Json).Version
$releaseName = "SkyrimNetMultiProxy-v$version"
$work = Join-Path $env:TEMP "proxy-launcher-release-build"
$stageDir = Join-Path $work $releaseName

$dllPath = Join-Path $root "build\Release\SkyrimNetMultiProxy.dll"
if (-not (Test-Path $dllPath)) {
    throw "SkyrimNetMultiProxy.dll not found at build\Release\SkyrimNetMultiProxy.dll -- build it first (CMake/Visual Studio, Release config)."
}

$stagingSrc = Join-Path $root "release-staging"
$externalFiles = @("proxy.py", "requirements.txt", "config.example.json", "proxy.ini.example", "start-proxy.bat", "LICENSE-proxy.txt")
foreach ($f in $externalFiles) {
    if (-not (Test-Path (Join-Path $stagingSrc $f))) {
        throw "release-staging\$f is missing. This script doesn't generate it -- copy the current version in from the private skyrimnet-multiproxy-dev repo (or a prior release zip, if nothing proxy-side changed) before running this."
    }
}

Write-Host "Building $releaseName (plugin v$version)..."

if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null

# 1. The SKSE plugin itself -- SkyrimNetMultiProxy.dll + SkyrimNetMultiProxy.ini -- goes into its
#    own importable archive at SKSE\Plugins\, not loose in the release folder. This is the whole
#    point of this script's existence: a mod manager import replaces "copy two files into Data\SKSE\
#    Plugins\ yourself" (confirmed real friction/confusion point).
Write-Host "Packaging SkyrimNetMultiProxy.zip (SKSE\Plugins\...)..."
$pluginStageDir = Join-Path $work "plugin-zip-stage"
$pluginDestDir = Join-Path $pluginStageDir "SKSE\Plugins"
New-Item -ItemType Directory -Path $pluginDestDir -Force | Out-Null
Copy-Item $dllPath (Join-Path $pluginDestDir "SkyrimNetMultiProxy.dll") -Force
Copy-Item (Join-Path $root "SkyrimNetMultiProxy.ini") (Join-Path $pluginDestDir "SkyrimNetMultiProxy.ini") -Force
Compress-Archive -Path (Join-Path $pluginStageDir "SKSE") -DestinationPath (Join-Path $stageDir "SkyrimNetMultiProxy.zip") -CompressionLevel Optimal

# 2. Everything else the release needs, sitting loose alongside SkyrimNetMultiProxy.zip.
Write-Host "Copying the rest of the release..."
Copy-Item (Join-Path $root "LICENSE") $stageDir -Force
Copy-Item (Join-Path $root "setup.bat") $stageDir -Force
Copy-Item (Join-Path $root "setup.ps1") $stageDir -Force
Copy-Item (Join-Path $root "START HERE.txt") $stageDir -Force
foreach ($f in $externalFiles) {
    Copy-Item (Join-Path $stagingSrc $f) $stageDir -Force
}

# 3. Zip it into github-releases\ (never loose at the project root -- matches vortex-collection-
#    tools' own convention, see that project's docs/DESIGN-GUIDE.md "Release packaging" section).
Write-Host "Creating zip..."
$releasesDir = Join-Path $root "github-releases"
New-Item -ItemType Directory -Path $releasesDir -Force | Out-Null
$outZip = Join-Path $releasesDir "$releaseName.zip"
if (Test-Path $outZip) { Remove-Item $outZip -Force }
Compress-Archive -Path (Join-Path $stageDir "*") -DestinationPath $outZip -CompressionLevel Optimal

Write-Host "Built: $outZip"
Write-Host "Staged (uncompressed) copy left at: $stageDir -- safe to delete."
