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
# The OUTER release zip wraps everything in a single top-level "SkyrimNet MultiProxy\" folder (rather
# than dumping loose files into wherever the user extracts), so an extract always leaves one tidy,
# clearly-named folder behind instead of scattering files into the surrounding directory. The inner
# SkyrimNetMultiProxy.zip is unaffected -- it still needs SKSE\Plugins\... at its own root for mod
# managers to deploy it correctly.
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
$wrapperName = "SkyrimNet MultiProxy"
$work = Join-Path $env:TEMP "proxy-launcher-release-build"
$stageDir = Join-Path $work $releaseName
$contentDir = Join-Path $stageDir $wrapperName

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

# --- Guard: this script is the safety net, not the only one (sync-release-staging.ps1 checks the
# same thing on the way in). Never publish list mirrors RELEASING.md's own "What must never cross"
# table -- keep this list in step by hand if that table changes; RELEASING.md is the authority.
$neverPublishNames = @("config.json", "proxy.ini", "pytest.ini", "requirements-dev.txt", "CLAUDE.md", "TECHNICAL.md", "README.md")
$neverPublishDirs = @("prompts", "tests", "design", "docs")

function Test-NeverPublishTree {
    param([string]$Dir, [string]$Label)
    if (-not (Test-Path $Dir -PathType Container)) { return }
    $offenders = @()
    Get-ChildItem $Dir -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        if ($neverPublishNames -contains $_.Name -or $_.Extension -eq ".log") { $offenders += $_.FullName }
    }
    Get-ChildItem $Dir -Recurse -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        if ($neverPublishDirs -contains $_.Name) { $offenders += $_.FullName }
    }
    if ($offenders.Count -gt 0) {
        Write-Host ""
        Write-Host "REFUSING TO BUILD -- $Label contains a file that must never be published" -ForegroundColor Red
        Write-Host "(see RELEASING.md's own 'What must never cross' table):" -ForegroundColor Red
        foreach ($o in $offenders) { Write-Host "  - $o" -ForegroundColor Red }
        throw "Aborting: forbidden file(s) present in $Label."
    }
}

Test-NeverPublishTree -Dir $stagingSrc -Label "release-staging\"

# --- Guard: config.example.json must actually look like a template, not a real config staged
# under the example's name. Shape-based, not vendor-specific -- flag any non-empty STRING value
# that doesn't look like an obvious placeholder, rather than pattern-matching one API key format.
# Recurses into objects/arrays (added when the Speeds tab's own "speed_thresholds" default --
# a nested object of real numbers, e.g. {"dialogue": {"good": 2.0, "slow": 4.0}, ...} -- started
# false-positiving here: numbers/booleans are never secret-shaped, so only strings are checked
# against the placeholder pattern, at any nesting depth, rather than stringifying a whole object
# and matching that against a pattern meant for flat scalars.
$exampleConfigPath = Join-Path $stagingSrc "config.example.json"
$exampleConfig = Get-Content $exampleConfigPath -Raw | ConvertFrom-Json
$placeholderPattern = '^(|YOUR[_-].*|CHANGE[_-]?ME|PLACEHOLDER|EXAMPLE|<.*>|x{3,})$'

function Test-SuspiciousValue {
    param($Value, [string]$Path)
    $found = @()
    if ($null -eq $Value) { return $found }
    if ($Value -is [string]) {
        if ($Value -notmatch $placeholderPattern) { $found += $Path }
    } elseif ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        $i = 0
        foreach ($item in $Value) {
            $found += Test-SuspiciousValue -Value $item -Path "$Path[$i]"
            $i++
        }
    } elseif ($Value -is [PSCustomObject]) {
        foreach ($prop in $Value.PSObject.Properties) {
            $found += Test-SuspiciousValue -Value $prop.Value -Path "$Path.$($prop.Name)"
        }
    }
    # Numbers, booleans, and anything else scalar-and-non-string are never secret-shaped -- allowed.
    return $found
}

$suspiciousKeys = @()
foreach ($prop in $exampleConfig.PSObject.Properties) {
    $suspiciousKeys += Test-SuspiciousValue -Value $prop.Value -Path $prop.Name
}
if ($suspiciousKeys.Count -gt 0) {
    Write-Host ""
    Write-Host "REFUSING TO BUILD -- config.example.json has a value that doesn't look like a" -ForegroundColor Red
    Write-Host "placeholder. Someone may have staged the real config.json under the example's name:" -ForegroundColor Red
    foreach ($k in $suspiciousKeys) { Write-Host "  - $k" -ForegroundColor Red }
    throw "Aborting: config.example.json in release-staging\ doesn't look like a template."
}

Write-Host "Building $releaseName (plugin v$version)..."

if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Path $contentDir -Force | Out-Null

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
Compress-Archive -Path (Join-Path $pluginStageDir "SKSE") -DestinationPath (Join-Path $contentDir "SkyrimNetMultiProxy.zip") -CompressionLevel Optimal

# 2. Everything else the release needs, sitting loose alongside SkyrimNetMultiProxy.zip, all inside
#    the "SkyrimNet MultiProxy\" wrapper folder so extracting the outer zip leaves one tidy folder.
Write-Host "Copying the rest of the release..."
Copy-Item (Join-Path $root "LICENSE") $contentDir -Force
Copy-Item (Join-Path $root "setup.bat") $contentDir -Force
Copy-Item (Join-Path $root "setup.ps1") $contentDir -Force
Copy-Item (Join-Path $root "START HERE.txt") $contentDir -Force
foreach ($f in $externalFiles) {
    Copy-Item (Join-Path $stagingSrc $f) $contentDir -Force
}

# 2b. Defense-in-depth: re-check the fully assembled tree right before it gets zipped, not just
#     its release-staging\ source. Catches anything the copy steps above could have introduced.
Test-NeverPublishTree -Dir $stageDir -Label "the assembled staging tree"

# 3. Zip it into github-releases\ (never loose at the project root -- matches vortex-collection-
#    tools' own convention, see that project's docs/DESIGN-GUIDE.md "Release packaging" section).
#    Compressing $contentDir itself (not "$contentDir\*") keeps "SkyrimNet MultiProxy\" as the
#    zip's own top-level entry, rather than flattening its contents into the archive root.
Write-Host "Creating zip..."
$releasesDir = Join-Path $root "github-releases"
New-Item -ItemType Directory -Path $releasesDir -Force | Out-Null
$outZip = Join-Path $releasesDir "$releaseName.zip"
if (Test-Path $outZip) { Remove-Item $outZip -Force }
Compress-Archive -Path $contentDir -DestinationPath $outZip -CompressionLevel Optimal

Write-Host "Built: $outZip"
Write-Host "Staged (uncompressed) copy left at: $stageDir -- safe to delete."

# 4. Check the zip before it goes anywhere (RELEASING.md step 5) -- list its contents and assert
#    the positives that doc names: one top-level "SkyrimNet MultiProxy\" folder, LICENSE-proxy.txt
#    present, no config.json/proxy.ini/.log/tests/prompts. A published zip can't be unpublished
#    from anyone who already has it, so this runs every time, not just when something looks wrong.
Write-Host ""
Write-Host "Verifying zip contents..."
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($outZip)
try {
    $entries = $zip.Entries | ForEach-Object { $_.FullName }
} finally {
    $zip.Dispose()
}

Write-Host ($entries -join "`n")
Write-Host ""

# @(...) forces an array even when Sort-Object -Unique finds only one distinct value -- without
# it, PowerShell unwraps a single result to a bare string, and [0] then indexes into its
# characters ('S') instead of the array, silently breaking this exact check.
$topLevelDirs = @($entries | ForEach-Object { ($_ -split '/')[0] } | Sort-Object -Unique)
if ($topLevelDirs.Count -ne 1 -or $topLevelDirs[0] -ne $wrapperName) {
    throw "Zip assertion failed: expected exactly one top-level folder named '$wrapperName\', found: $($topLevelDirs -join ', ')"
}

if (-not ($entries -contains "$wrapperName/LICENSE-proxy.txt")) {
    throw "Zip assertion failed: $wrapperName\LICENSE-proxy.txt is missing."
}

$forbiddenInZip = $entries | Where-Object {
    $leaf = ($_ -split '/')[-1]
    $neverPublishNames -contains $leaf -or
    $leaf -like "*.log" -or
    (($_ -split '/') | Where-Object { $neverPublishDirs -contains $_ })
}
if ($forbiddenInZip) {
    Write-Host "ZIP ASSERTION FAILED -- forbidden entries found in the built zip:" -ForegroundColor Red
    foreach ($f in $forbiddenInZip) { Write-Host "  - $f" -ForegroundColor Red }
    throw "Aborting: $outZip contains file(s) that must never be published."
}

Write-Host "All zip content assertions passed: one top-level '$wrapperName\' folder, LICENSE-proxy.txt present, no config.json/proxy.ini/.log/tests/prompts." -ForegroundColor Green
