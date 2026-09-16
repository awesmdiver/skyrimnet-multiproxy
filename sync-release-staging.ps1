# Stages the six files RELEASING.md's manifest names, and only those six, from the private
# skyrimnet-multiproxy-dev repo into release-staging\ (gitignored) here.
#
# Why this exists (read RELEASING.md's own "What crosses from dev to public" table first): the
# hand-copy step this replaces is the one place a private file can leak into a published zip.
# release-staging\ holds config.json's own template neighbor, config.example.json -- one glob or one
# tab-completion slip away from staging the REAL config.json instead, which carries live API keys
# in plaintext. This script never globs and never copies a folder: every file below is named
# explicitly, both the source it comes from and the name it lands under.
#
# Usage:
#   .\sync-release-staging.ps1
#
# Requires: skyrimnet-multiproxy-dev checked out as a sibling of this repo (..\skyrimnet-multiproxy-
# dev, i.e. both under the same parent folder) -- see docs/DESIGN-GUIDE.md's own "Path handling"
# section for why this is resolved from $PSScriptRoot rather than hardcoded.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$stagingDir = Join-Path $root "release-staging"

# --- Resolve the dev repo as a sibling, never a hardcoded absolute path ---
$devRepo = Join-Path $root "..\skyrimnet-multiproxy-dev"
if (-not (Test-Path $devRepo -PathType Container)) {
    throw (
        "skyrimnet-multiproxy-dev not found at $devRepo. This script expects it checked out as a " +
        "sibling of this repo -- both under the same parent folder. Clone it there first."
    )
}
$devRepo = (Resolve-Path $devRepo).Path

# --- Refuse to proceed if a never-publish file is already sitting in release-staging\ ---
# Mirrors RELEASING.md's own "What must never cross" table -- keep this list in step by hand if
# that table changes; RELEASING.md is the authority, this is just its enforcement.
$neverPublishNames = @("config.json", "proxy.ini", "pytest.ini", "requirements-dev.txt", "CLAUDE.md", "TECHNICAL.md", "README.md")
$neverPublishDirs = @("prompts", "tests", "design", "docs")

if (Test-Path $stagingDir -PathType Container) {
    $offenders = @()
    foreach ($name in $neverPublishNames) {
        $p = Join-Path $stagingDir $name
        if (Test-Path $p -PathType Leaf) { $offenders += $p }
    }
    foreach ($name in $neverPublishDirs) {
        $p = Join-Path $stagingDir $name
        if (Test-Path $p -PathType Container) { $offenders += $p }
    }
    $logFiles = Get-ChildItem $stagingDir -Filter "*.log" -File -ErrorAction SilentlyContinue
    if ($logFiles) { $offenders += $logFiles.FullName }

    if ($offenders.Count -gt 0) {
        Write-Host ""
        Write-Host "REFUSING TO PROCEED -- release-staging\ already contains a file that must never" -ForegroundColor Red
        Write-Host "be published (see RELEASING.md's own 'What must never cross' table):" -ForegroundColor Red
        foreach ($o in $offenders) { Write-Host "  - $o" -ForegroundColor Red }
        Write-Host ""
        Write-Host "Not deleted automatically -- someone copied the wrong thing and needs to know." -ForegroundColor Red
        throw "Aborting: forbidden file(s) present in release-staging\."
    }
}

New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

# --- The manifest, by explicit name. Exactly six files, never a folder copy, never a wildcard. ---
$manifest = @(
    @{ Source = "proxy.py"; Dest = "proxy.py" }
    @{ Source = "requirements.txt"; Dest = "requirements.txt" }
    @{ Source = "config.example.json"; Dest = "config.example.json" }
    @{ Source = "proxy.ini.example"; Dest = "proxy.ini.example" }
    @{ Source = "start-proxy.bat"; Dest = "start-proxy.bat" }
    @{ Source = "LICENSE"; Dest = "LICENSE-proxy.txt" }
)

Write-Host "Staging from: $devRepo"
Write-Host "Staging into: $stagingDir"
Write-Host ""

$results = @()
foreach ($entry in $manifest) {
    $srcPath = Join-Path $devRepo $entry.Source
    $dstPath = Join-Path $stagingDir $entry.Dest

    if (-not (Test-Path $srcPath -PathType Leaf)) {
        throw "Manifest source missing: $srcPath (expected in the dev repo root -- has it moved? update RELEASING.md and this script together)."
    }

    # "Changed since the last release" -- compared before overwriting, since Copy-Item -Force below
    # destroys the previous staged copy this comparison needs.
    $changed = $true
    if (Test-Path $dstPath -PathType Leaf) {
        $oldHash = (Get-FileHash $dstPath -Algorithm SHA256).Hash
        $newHash = (Get-FileHash $srcPath -Algorithm SHA256).Hash
        $changed = $oldHash -ne $newHash
    }

    Copy-Item -Path $srcPath -Destination $dstPath -Force

    $results += [pscustomobject]@{
        File    = $entry.Dest
        From    = $entry.Source
        Changed = if ($changed) { "changed" } else { "unchanged" }
    }
}

Write-Host "Staged:"
$results | ForEach-Object {
    $label = if ($_.From -eq $_.File) { $_.File } else { "$($_.File) (from $($_.From))" }
    Write-Host ("  {0,-24} {1}" -f $label, $_.Changed)
}
Write-Host ""
Write-Host "Done. Next: .\build-release.ps1"
