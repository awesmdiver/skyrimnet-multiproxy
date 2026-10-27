<#
.SYNOPSIS
  Builds the two Windows installers into dist\ : MultiProxy-Setup-<version>.exe and MultiProxy-Courier-Setup-<version>.exe.

.DESCRIPTION
  Finds a Python 3.14.10 (the build copies tkinter from it into the installer's own Python, so it must be that exact version) and Inno Setup,
  then runs installer\build.py. Nothing is installed on this device except, if you say yes, Inno Setup itself (free, per-user, via winget).

.PARAMETER Only         Build just one:  multiproxy  or  courier.
.PARAMETER ModPackage   The Skyrim mod package (SkyrimNetMultiProxy.zip) to put inside the MultiProxy installer. Without it the build looks in the
                        public repo's release-staging folder and stops with a plain message if it isn't there yet.
.PARAMETER Yes          Answer yes to "install Inno Setup now?".
#>
[CmdletBinding()]
param(
    [ValidateSet('multiproxy', 'courier')][string]$Only = '',
    [string]$ModPackage = '',
    [switch]$Yes
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Find-Python {
    foreach ($cand in @(@('py', '-3.14'), @('python'), @('python3'))) {
        $cmd = Get-Command $cand[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $extra = @(); if ($cand.Count -gt 1) { $extra = $cand[1..($cand.Count - 1)] }
        try {
            $out = & $cmd.Source @extra -c "import sys; print(sys.version.split()[0]); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and @($out).Count -ge 2 -and $out[0] -eq '3.14.10') { return $out[1] }
        } catch { }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "This build needs Python 3.14.10 (it copies tkinter from it into the installer's own Python)." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/release/python-31410/ and run this again."
    exit 1
}

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "Inno Setup isn't installed (it builds the installer; free)."
    $answer = if ($Yes) { 'y' } else { Read-Host "Install it now with winget (no admin needed)? [Y/n]" }
    if ($answer -eq '' -or $answer -match '^(y|yes)$') {
        winget install --id JRSoftware.InnoSetup --scope user --silent --accept-package-agreements --accept-source-agreements
        $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $iscc) { Write-Host "Inno Setup is needed. Install it with:  winget install --id JRSoftware.InnoSetup --scope user" -ForegroundColor Red; exit 1 }
}

$args2 = @("$PSScriptRoot\build.py", '--iscc', $iscc)
if ($Only) { $args2 += @('--only', $Only) }
if ($ModPackage) { $args2 += @('--mod-package', $ModPackage) }
& $python @args2
exit $LASTEXITCODE
