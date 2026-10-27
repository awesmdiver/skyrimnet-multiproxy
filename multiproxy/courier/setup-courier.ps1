#Requires -Version 7.0
<#
.SYNOPSIS
  Sets up THIS computer as a MultiProxy Courier (run by "Set up this PC.bat").

.DESCRIPTION
  Installs what's needed (Python, if missing), copies the Courier into its own folder with a private
  Python environment, asks ONCE to let it through the Windows firewall on private (home) networks only,
  makes it start with Windows, starts it, and shows the pairing code. Safe to run again: it updates the
  program and keeps your settings and pairing code.

.PARAMETER InstallDir   Where to put it. Setup asks first, offering %APPDATA%\MultiProxy Courier (or the folder of an existing install,
                        so running setup again updates it in place). Giving this parameter skips the question.
.PARAMETER Port         The Courier's port on this device. Default 8110.
.PARAMETER NoAutoStart  Don't make it start with Windows.
.PARAMETER NoFirewall   Don't add the firewall rule (for testing).
.PARAMETER NoStart      Set everything up but don't start it (for testing).
.PARAMETER Yes          Answer "yes" to every question and keep the suggested folder (for unattended setup).
.PARAMETER NoShortcut   Don't add the "MultiProxy Courier" Start menu shortcut (for testing).
.PARAMETER StartMenuDir Where the Start menu shortcut goes. Default: this user's own Start menu Programs folder (no admin needed).

  Run from the folder the Courier is already installed in (after copying a newer package over it), setup skips the copy
  and updates everything else in place.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = '',
    [int]$Port = 8110,
    [switch]$NoAutoStart,
    [switch]$NoFirewall,
    [switch]$NoStart,
    [switch]$Yes,
    [switch]$NoShortcut,
    [string]$StartMenuDir = ''
)

$ErrorActionPreference = 'Stop'
$source = $PSScriptRoot
$discoveryPort = 8099
function Say($text)  { Write-Host $text }
function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "Setup stopped: $text" -ForegroundColor Red; exit 1 }
# THE list of the Courier's files is courier-files.json, next to this script (the zip build, the installers and the release signer read the same file).
function Get-ProgramFiles {
    try {
        $fileList = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'courier-files.json') -Encoding UTF8 | ConvertFrom-Json
        return @($fileList.program) + @($fileList.data.PSObject.Properties.Name) + @('Start Courier.bat')
    } catch {
        Fail "the setup package is missing its file list (courier-files.json). Please download MultiProxy Courier again and extract all files."
    }
}
function Ask($question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$question [Y/n]"
    return ($answer -eq '' -or $answer -match '^(y|yes)$')
}

# ---- Where to install --------------------------------------------------------------------------------
function Get-InstallCandidates {
    # Where this setup has put the Courier before (older versions used LOCALAPPDATA), then where it puts it now.
    return @((Join-Path $env:APPDATA 'MultiProxy Courier'), (Join-Path $env:LOCALAPPDATA 'MultiProxy Courier'))
}
function Get-RealPath([string]$p) {
    # The real, resolved place a folder is: full path, short names expanded, and a link / junction followed to what it points at.
    $full = [IO.Path]::GetFullPath($p)
    try {
        if (Test-Path -LiteralPath $full) {
            $item = Get-Item -LiteralPath $full -Force
            $target = $item.ResolveLinkTarget($true)
            if ($target) { $full = $target.FullName }
            $full = (Resolve-Path -LiteralPath $full).ProviderPath
        }
    } catch { }
    return $full.TrimEnd('\', '/')
}
function Test-SameFolder([string]$a, [string]$b) { return ((Get-RealPath $a) -ieq (Get-RealPath $b)) }
function Test-IsInstall([string]$dir) {
    return ((Test-Path -LiteralPath (Join-Path $dir 'courier.py')) -and (Test-Path -LiteralPath (Join-Path $dir '.venv\Scripts\python.exe')))
}
function Find-ExistingInstall {
    if ($source -and (Test-IsInstall $source)) { return $source }        # setup run from inside an installed Courier's own folder: that is the install
    foreach ($c in (Get-InstallCandidates)) { if (Test-Path -LiteralPath (Join-Path $c 'courier.py')) { return $c } }
    return $null
}
function Test-InstallFolder([string]$dir) {
    # $null when a folder can be made and written to; otherwise the plain reason it can't.
    try {
        New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
        $probe = Join-Path $dir ('.write-test-' + [guid]::NewGuid().ToString('N'))
        Set-Content -LiteralPath $probe -Value 'ok' -ErrorAction Stop
        Remove-Item -LiteralPath $probe -Force -ErrorAction Stop
        return $null
    } catch { return $_.Exception.Message }
}
function Resolve-InstallDir([string]$given, [bool]$unattended, [scriptblock]$readLine) {
    # The folder to install into: the one given on the command line, else the suggested one (unattended), else whatever the player
    # typed (Enter keeps the suggestion). A folder that can't be written to gets a plain message and the question again.
    $existing = Find-ExistingInstall
    $default = if ($existing) { $existing } else { Join-Path $env:APPDATA 'MultiProxy Courier' }
    if ($given) {
        $full = [IO.Path]::GetFullPath($given)
        $why = Test-InstallFolder $full
        if ($why) { Fail "Cannot install to ${full}: $why" }
        return $full
    }
    if ($unattended) {
        $why = Test-InstallFolder $default
        if ($why) { Fail "Cannot install to ${default}: $why" }
        return $default
    }
    while ($true) {
        Say ""
        Say "Where should the Courier be installed?"
        if ($existing) { Say "Courier is already installed here. Press Enter to update:" } else { Say "Press Enter to use:" }
        Say "  $default"
        Say "Or type another folder and press Enter:"
        $typed = ([string](& $readLine)).Trim().Trim('"')
        if (-not $typed) { $typed = $default }
        $typed = [Environment]::ExpandEnvironmentVariables($typed)
        if (-not [IO.Path]::IsPathRooted($typed)) { Say "Please type a full folder path, like D:\Courier."; continue }
        $full = [IO.Path]::GetFullPath($typed)
        $why = Test-InstallFolder $full
        if ($why) { Say "Can't save to that folder ($why). Please pick another."; continue }
        return $full
    }
}
function Find-InstallerCourier([string]$Key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{2E0B1236-BECF-4DE3-A240-EAFDFE8EA7DF}_is1') {
    # The folder of a Courier put here by the Windows installer (it is in the Apps list), or $null. The installer's program is in a "python" folder inside it.
    try {
        $p = Get-ItemProperty -LiteralPath $Key -ErrorAction Stop
        $dir = [string]$p.InstallLocation
        if (-not $dir) { $dir = [string]$p.'Inno Setup: App Path' }
        $dir = $dir.Trim().TrimEnd('\')
        if ($dir -and (Test-Path -LiteralPath (Join-Path $dir 'python\python.exe'))) { return $dir }
    } catch { }
    return $null
}
if ($env:COURIER_INSTALL_LIB -eq '1') { return }      # (tests load the functions above without running setup)

function Find-Python {
    foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
        $cmd = Get-Command $cand[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $extra = @(); if ($cand.Count -gt 1) { $extra = $cand[1..($cand.Count - 1)] }
        try {
            # Windows' own "python" placeholder (the Store stub) prints nothing and fails: it doesn't count.
            $out = & $cmd.Source @extra -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -ne 0 -or @($out).Count -lt 2) { continue }
            if ([version]$out[0] -ge [version]'3.10') { return $out[1] }
        } catch { }
    }
    return $null
}

Say "Setting up this device as a MultiProxy Courier."
Say "This device will run AI models for your main device using its graphics card."

# ---- A Courier from the Windows installer is already here: never run two ----------------------------------------------------
$fromInstaller = Find-InstallerCourier
if ($fromInstaller) {
    Write-Host ""
    Write-Host "MultiProxy Courier is already installed in $fromInstaller. Please uninstall it in Windows Settings, then run setup again." -ForegroundColor Yellow
    exit 1
}

# ---- Where to put it (asked before anything is copied) ----------------------------------------------------
$InstallDir = Resolve-InstallDir $InstallDir ([bool]($Yes -or [Console]::IsInputRedirected)) { Read-Host }

# ---- Python ------------------------------------------------------------------------------------
Step "Checking Python..."
$python = Find-Python
if (-not $python) {
    Say "Courier needs Python to run."
    if (-not (Ask "Install Python now (takes about a minute)?")) {
        Fail "Setup can't continue without Python. Install it from https://www.python.org/downloads/ (tick 'Add Python to PATH') and run setup again."
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "I can't install Python for you automatically. Please install it from https://www.python.org/downloads/ and run setup again."
    }
    winget install --id Python.Python.3.12 -e --scope user --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { Fail "Python didn't install. Try running setup again, or install it manually from https://www.python.org/downloads/." }
    $python = Find-Python
    if (-not $python) {
        $guess = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
        if (Test-Path $guess) { $python = $guess }
    }
    if (-not $python) { Fail "Python finished installing, but Windows hasn't registered it yet. Close this window and double-click 'Set up this PC' again." }
}

# ---- An older "MultiProxy Model Server" on this device ------------------------------------------------------
$oldShortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'MultiProxy Model Server.lnk'
if (Test-Path $oldShortcut) {
    Step "I found an older version on this device."
    Say "Courier replaces it. Running both at once will cause problems."
    if (Ask "Stop the old version from starting with Windows? (Its files stay safe.)") {
        Remove-Item -LiteralPath $oldShortcut -Force
        Say "Done. Please close the old version if it's running."
    }
}

# ---- A Courier that is already running here (this is an update) ------------------------------------------------
# Copying new files under a running Courier would leave the OLD program running (and the new one unable to start), so stop it first.
# Your settings and pairing code live in courier.json and are not touched.
$serving = @()
$serving += @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess })
$serving += @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like (Join-Path $InstallDir '.venv\*') } | ForEach-Object { $_.ProcessId })
$serving = @($serving | Where-Object { $_ -and $_ -ne $PID } | Sort-Object -Unique)
if ($serving.Count -gt 0) {
    Step "Courier is already running. Closing it to update..."
    foreach ($id in $serving) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

# ---- Copy the program ------------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$inPlace = Test-SameFolder $source $InstallDir
$programFiles = Get-ProgramFiles
if ($inPlace) {
    # The new files were already copied over the install folder by hand: copying a file onto itself fails, so there is nothing to copy.
    Step "Updating Courier in this folder."
    foreach ($f in $programFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $f))) { Fail "A setup file is missing ($f). Please download MultiProxy Courier again and extract all files." }
    }
} else {
    Step "Installing Courier to $InstallDir..."
    foreach ($f in $programFiles) {
        $from = Join-Path $source $f
        if (-not (Test-Path $from)) { Fail "The setup package is missing $f. Please download MultiProxy Courier again." }
        Copy-Item -LiteralPath $from -Destination (Join-Path $InstallDir $f) -Force
    }
    # (the date and commit this package was built from: the Courier shows it and sends it, so a build can be told apart; not needed to run)
    if (Test-Path -LiteralPath (Join-Path $source 'courier-build.txt')) { Copy-Item -LiteralPath (Join-Path $source 'courier-build.txt') -Destination (Join-Path $InstallDir 'courier-build.txt') -Force }
}

# ---- The Start menu shortcut (so it can be started without running setup again) ----------------------------------
if (-not $NoShortcut) {
    try {
        $menu = if ($StartMenuDir) { $StartMenuDir } else { [Environment]::GetFolderPath('Programs') }
        New-Item -ItemType Directory -Force -Path $menu | Out-Null
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut((Join-Path $menu 'MultiProxy Courier.lnk'))
        $lnk.TargetPath = (Join-Path $InstallDir 'Start Courier.bat')
        $lnk.WorkingDirectory = $InstallDir
        $lnk.WindowStyle = 7
        $lnk.Description = 'Start the MultiProxy Courier'
        $icon = Join-Path $InstallDir 'courier.ico'
        if (Test-Path -LiteralPath $icon) { $lnk.IconLocation = $icon }
        $lnk.Save()
    } catch { Say "(I couldn't add a Start menu shortcut. You can still double-click 'Start Courier' in $InstallDir.)" }
}

# ---- Private Python environment ---------------------------------------------------------------------
Step "Setting up Python..."
$venvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
$venvPythonW = Join-Path $InstallDir '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $venvPython)) {
    & $python -m venv (Join-Path $InstallDir '.venv')
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't set up Python." }
}
& $venvPython -m pip install -q --disable-pip-version-check -r (Join-Path $InstallDir 'requirements-courier.txt')
if ($LASTEXITCODE -ne 0) { Fail "Couldn't install required files. Please check your internet connection and run setup again." }

# ---- Settings + pairing code ------------------------------------------------------------------------------
Step "Generating this device's unique pairing code..."
Push-Location $InstallDir
try {
    $args2 = @('courier.py', 'code', '--port', "$Port")
    if ($NoAutoStart) { $args2 += @('--start-with-windows', 'off') }
    $code = (& $venvPython @args2 | Select-Object -Last 1)
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0 -or -not $code) { Fail "Couldn't save settings on this device." }

# ---- The firewall: ask once, private networks only -------------------------------------------------------------
# Reading the rules needs no administrator. They count as "in place" only when there are exactly two "MultiProxy Courier" rules and they are the right ones:
# enabled, inbound, allow, private networks, from this network only (RemoteIP LocalSubnet), for this Courier's own program when it is known, TCP on this Courier's port and
# UDP on the discovery port. Anything else (a changed port, a missing rule, duplicates,
# a Windows whose netsh prints other words) is asked about as before, and the old rules are all deleted before the two right ones are added: never more than two.
function Test-CourierFirewall([string]$Text, [int]$TcpPort, [int]$UdpPort, [string]$Program = '') {
    $blocks = @($Text -split '(?m)^\s*Rule Name:' | Select-Object -Skip 1)
    if ($blocks.Count -ne 2) { return $false }
    $tcp = $false; $udp = $false
    foreach ($b in $blocks) {
        $f = @{}
        foreach ($line in ($b -split "`r?`n")) { if ($line -match '^\s*([A-Za-z ]+?):\s+(.*?)\s*$') { $f[$Matches[1]] = $Matches[2] } }
        if ($f['Enabled'] -ne 'Yes' -or $f['Direction'] -ne 'In' -or $f['Action'] -ne 'Allow' -or $f['Profiles'] -ne 'Private') { return $false }
        if (-not "$($f['RemoteIP'])".StartsWith('LocalSubnet')) { return $false }
        if ($Program -and "$($f['Program'])" -ine $Program) { return $false }
        if ($f['Protocol'] -eq 'TCP' -and $f['LocalPort'] -eq "$TcpPort") { $tcp = $true }
        elseif ($f['Protocol'] -eq 'UDP' -and $f['LocalPort'] -eq "$UdpPort") { $udp = $true }
        else { return $false }
    }
    return ($tcp -and $udp)
}
# The commands that add the two rules (run elevated, after Windows' own OK prompt). Private networks only, this network only (LocalSubnet), and only for the Courier's own
# program when it is known (a path with an apostrophe is doubled for the single quotes it sits in).
function New-CourierFirewallCommand([int]$TcpPort, [int]$UdpPort, [string]$Program = '') {
    $prog = ''
    if ($Program) { $prog = " program='" + ($Program -replace "'", "''") + "'" }
    $tail = " dir=in action=allow profile=private remoteip=LocalSubnet"
    return "netsh advfirewall firewall delete rule name='MultiProxy Courier' | Out-Null; " +
           "netsh advfirewall firewall add rule name='MultiProxy Courier'$tail protocol=TCP localport=$TcpPort$prog | Out-Null; " +
           "netsh advfirewall firewall add rule name='MultiProxy Courier'$tail protocol=UDP localport=$UdpPort$prog | Out-Null"
}
# The program that listens: the real Python behind the environment (a virtual environment's own pythonw.exe starts it), or none when that can't be told.
function Get-CourierListenerProgram([string]$VenvPython) {
    try {
        $base = (& $VenvPython -c "import sys, os; print(os.path.join(os.path.dirname(sys._base_executable), 'pythonw.exe'))" 2>$null | Select-Object -Last 1)
        if ($base -and (Test-Path -LiteralPath $base)) { return $base }
    } catch { }
    return ''
}
$firewall = 'unknown'
if (-not $NoFirewall) {
    Step "Allowing your main device to connect..."
    $existing = ''
    try { $existing = (netsh advfirewall firewall show rule "name=MultiProxy Courier" 2>$null | Out-String) } catch { }
    $fwProgram = Get-CourierListenerProgram $venvPython
    if (Test-CourierFirewall $existing $Port $discoveryPort $fwProgram) {
        $firewall = 'added'
        Say "The firewall rule is already in place."
    } else {
    Say "I need to add a firewall rule so your main device can reach this one."
    Say "Windows will ask for permission in a pop-up."
    if (Ask "Add the rule now? Press Yes when Windows asks.") {
        $rule = New-CourierFirewallCommand $Port $discoveryPort $fwProgram
        try {
            Start-Process -FilePath (Get-Command pwsh).Source -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList @('-NoProfile', '-Command', $rule)
            $present = (netsh advfirewall firewall show rule name='MultiProxy Courier' 2>$null | Out-String) -match 'MultiProxy Courier'
            if ($present) { $firewall = 'added'; Say "Done." } else { $firewall = 'declined' }
        } catch { $firewall = 'declined' }
    } else { $firewall = 'declined' }
    if ($firewall -eq 'declined') {
        Say "No firewall rule was added. Your main device might not find this device until you allow it."
    }
    }
    Push-Location $InstallDir
    try { & $venvPython courier.py code --firewall $firewall | Out-Null } finally { Pop-Location }
}

if ($NoStart) { Say ""; Say "Setup complete (Courier was not started)."; exit 0 }

# ---- Start it and show the code --------------------------------------------------------------------------
Step "Starting Courier..."
Start-Process -FilePath $venvPythonW -ArgumentList @('courier.py', 'run', '--show-ready-window') -WorkingDirectory $InstallDir -WindowStyle Hidden
$up = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    Push-Location $InstallDir
    try { $status = (& $venvPython courier.py status | Out-String) } finally { Pop-Location }
    if ($status -match 'is running') { $up = $true; break }
}
if (-not $up) { Fail "Courier didn't start. Try running setup again." }

Write-Host ""
Write-Host "All set. This device is now a MultiProxy Courier." -ForegroundColor Green
Write-Host ""
Push-Location $InstallDir
try { $pairedWith = ((& $venvPython courier.py paired | Out-String).Trim()) } finally { Pop-Location }
if ($pairedWith) {
    Write-Host "  This device is already paired with $pairedWith, so just leave it running." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "There is nothing more to do. (To pair a different main device, use 'Show pairing code' from the Courier's tray icon.)"
} else {
    Write-Host "  Pairing code:  $code" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Next: open Other devices in MultiProxy on your main device. Type the code above and click Pair."
}
Write-Host ""
Write-Host "(Courier will keep running quietly by your clock.)"
Write-Host "To start it later, open MultiProxy Courier from the Start menu."
