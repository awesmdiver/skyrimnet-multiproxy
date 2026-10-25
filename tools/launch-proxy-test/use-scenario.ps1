<#
.SYNOPSIS
    Copies one of the two ini-scenarios templates into bin\Data\SKSE\Plugins\SkyrimNetMultiProxy.ini,
    filling in {BINDIR} with this folder's real resolved bin\ path.

.PARAMETER Scenario
    "success" or "failure" -- see ini-scenarios\*.ini.template for what each one exercises.

.EXAMPLE
    .\use-scenario.ps1 failure
    .\bin\test_launch.exe
#>
param(
    [Parameter(Mandatory)][ValidateSet("success", "failure")]
    [string]$Scenario,

    # Folder holding the real proxy.py (the {DEVDIR} in success.ini.template). Defaults to a skyrimnet-multiproxy-dev checkout
    # next to this repository.
    [string]$DevDir = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$binDir = Join-Path $PSScriptRoot "bin"
$pluginsDir = Join-Path $binDir "Data\SKSE\Plugins"
New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null

$templatePath = Join-Path $PSScriptRoot "ini-scenarios\$Scenario.ini.template"
$content = Get-Content $templatePath -Raw
$content = $content.Replace("{BINDIR}", $binDir)
if (-not $DevDir) { $DevDir = Join-Path $PSScriptRoot "..\..\..\skyrimnet-multiproxy-dev" }
if ($content.Contains("{DEVDIR}")) {
    if (-not (Test-Path (Join-Path $DevDir "proxy.py"))) { throw "proxy.py not found in $DevDir. Pass -DevDir <the folder that holds proxy.py>." }
    $content = $content.Replace("{DEVDIR}", (Resolve-Path $DevDir).Path)
}

$destPath = Join-Path $pluginsDir "SkyrimNetMultiProxy.ini"
Set-Content -Path $destPath -Value $content -NoNewline
Write-Host "Wrote $destPath (scenario: $Scenario)" -ForegroundColor Green
