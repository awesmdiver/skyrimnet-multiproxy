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
    [string]$Scenario
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$binDir = Join-Path $PSScriptRoot "bin"
$pluginsDir = Join-Path $binDir "Data\SKSE\Plugins"
New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null

$templatePath = Join-Path $PSScriptRoot "ini-scenarios\$Scenario.ini.template"
$content = Get-Content $templatePath -Raw
$content = $content.Replace("{BINDIR}", $binDir)

$destPath = Join-Path $pluginsDir "SkyrimNetMultiProxy.ini"
Set-Content -Path $destPath -Value $content -NoNewline
Write-Host "Wrote $destPath (scenario: $Scenario)" -ForegroundColor Green
