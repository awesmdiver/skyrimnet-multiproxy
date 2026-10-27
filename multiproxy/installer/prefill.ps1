<#
  What an installer pre-fills on its folder page, and the small note it leaves for the next run (2026-10-06).

  Used by installer\courier.iss and installer\multiproxy.iss (extracted at the start of Setup, run hidden, no administrator).

    -Mode discover   works out the answers and writes them to -Out as "key=value" lines (UTF-8)
    -Mode write      saves the answers just given as the registry note (HKCU\<RegKey>)

  Each value is taken from the first place that has it:
    (a) the earlier install's own settings file (courier.json / config.json): its saved models folder, its start-with-Windows choice
    (b) the registry note this script wrote on the last install
    (c) the earlier install itself: the folder an older script-made Courier lives in, its start-with-Windows entry, its Start menu shortcut
    (d) the defaults
  A value the settings file holds always wins over the note (the player may have changed it later in the dashboard).
  Nothing here changes anything in discover mode, and every read is guarded: a damaged or missing file, key or folder
  just means "next source".

  Every place it looks can be moved for a test (-AppData, -LocalAppData, -RunKey, -StartMenuDir, -RegKey), so a test never reads or
  writes this device's real registry key or folders.
#>
param(
  [Parameter(Mandatory = $true)][ValidateSet('courier', 'multiproxy')][string]$App,
  [Parameter(Mandatory = $true)][ValidateSet('discover', 'write')][string]$Mode,
  [string]$Out = '',
  [string]$RegKey = '',
  [string]$AppData = $env:APPDATA,
  [string]$LocalAppData = $env:LOCALAPPDATA,
  [string]$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
  [string]$StartMenuDir = '',
  [string]$DefaultInstall = '',
  [string]$PreviousInstall = '',
  [string]$DefaultSettings = '',
  [string]$Install = '', [string]$Settings = '', [string]$Models = '',
  [string]$StartWithWindows = '', [string]$StartMenu = ''
)
$ErrorActionPreference = 'Stop'

$courier = ($App -eq 'courier')
$appName = if ($courier) { 'MultiProxy Courier' } else { 'SkyrimNet MultiProxy' }
$settingsFile = if ($courier) { 'courier.json' } else { 'config.json' }
if (-not $RegKey) { $RegKey = "Software\$appName" }
$noteKey = "Registry::HKEY_CURRENT_USER\$RegKey"
if (-not $StartMenuDir) { $StartMenuDir = Join-Path $AppData 'Microsoft\Windows\Start Menu\Programs' }

function Clean([string]$s) { return ([string]$s).Trim().Trim('"').TrimEnd('\') }

function Read-Note([string]$name) {
  try { return Clean ((Get-ItemProperty -LiteralPath $noteKey -ErrorAction Stop).$name) } catch { return '' }
}

if ($Mode -eq 'write') {
  New-Item -Path $noteKey -Force | Out-Null
  $values = [ordered]@{ InstallDir = $Install; SettingsDir = $Settings; ModelsDir = $Models; StartWithWindows = $StartWithWindows; StartMenu = $StartMenu }
  foreach ($k in $values.Keys) { New-ItemProperty -LiteralPath $noteKey -Name $k -Value ([string]$values[$k]) -PropertyType String -Force | Out-Null }
  exit 0
}

# ---- discover ------------------------------------------------------------------------------------------------------------------
function Read-Settings([string]$dir) {
  # The earlier install's settings as an object, or $null when there is no file or it can't be read (damaged, empty, not an object).
  try {
    $p = Join-Path $dir $settingsFile
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { return $null }
    $o = Get-Content -LiteralPath $p -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($o -is [pscustomobject]) { return $o }
  } catch { }
  return $null
}
function Has-SettingsFile([string]$dir) { try { return (Test-Path -LiteralPath (Join-Path $dir $settingsFile) -PathType Leaf) } catch { return $false } }
function Is-OlderScriptCourier([string]$dir) {
  # The zip-file / "Set up this PC" Courier: program and settings in one folder, the program in a .venv (or started by Start Courier.bat).
  if (-not $courier -or -not $dir) { return $false }
  try {
    return ((Test-Path -LiteralPath (Join-Path $dir 'courier.py')) -and ((Test-Path -LiteralPath (Join-Path $dir '.venv')) -or (Test-Path -LiteralPath (Join-Path $dir 'Start Courier.bat'))))
  } catch { return $false }
}
function Config-DirOf([string]$commandLine) {
  # "... --config-dir "D:\x y" ..." or "... --config-dir D:\x ..." -> the folder
  if ($commandLine -match '--config-dir\s+"([^"]+)"') { return Clean $Matches[1] }
  if ($commandLine -match '--config-dir\s+(\S+)') { return Clean $Matches[1] }
  return ''
}
function Parent-Of([string]$path) { try { if ($path) { return (Split-Path -Parent $path) } } catch { }; return '' }
function Folder-OfFile([string]$commandLine) {
  # the folder of the first quoted path ending in .bat / .py / .exe in a start command, or ''
  if ($commandLine -match '"([^"]+\.(?:bat|py|exe))"') { try { return (Split-Path -Parent $Matches[1]) } catch { } }
  return ''
}

$candidates = New-Object System.Collections.ArrayList     # @{ dir; why }
function Add-Candidate([string]$dir, [string]$why) {
  $d = Clean $dir
  if ($d -and -not ($candidates | Where-Object { $_.dir -ieq $d })) { [void]$candidates.Add(@{ dir = $d; why = $why }) }
}

$noteInstall = Read-Note 'InstallDir'; $noteSettings = Read-Note 'SettingsDir'; $noteModels = Read-Note 'ModelsDir'
$noteWin = Read-Note 'StartWithWindows'; $noteMenu = Read-Note 'StartMenu'

# The start-with-Windows entry and the Start menu shortcut of an earlier install say where it lives and which settings folder it was told to use.
$runCommand = ''
try { $runCommand = [string](Get-ItemProperty -LiteralPath $RunKey -ErrorAction Stop).$appName } catch { }
$shortcut = Join-Path $StartMenuDir "$appName.lnk"
$shortcutTarget = ''; $shortcutArgs = ''; $shortcutDir = ''
if (Test-Path -LiteralPath $shortcut -PathType Leaf) {
  try {
    $l = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    $shortcutTarget = [string]$l.TargetPath; $shortcutArgs = [string]$l.Arguments; $shortcutDir = [string]$l.WorkingDirectory
  } catch { }
}

Add-Candidate $noteSettings 'note'
if ($runCommand) { Add-Candidate (Config-DirOf $runCommand) 'run-entry' }
if ($shortcutArgs) { Add-Candidate (Config-DirOf $shortcutArgs) 'shortcut' }
if ($PreviousInstall) {
  try { $loc = Clean (Get-Content -LiteralPath (Join-Path $PreviousInstall 'settings-location.txt') -Raw -ErrorAction Stop); Add-Candidate $loc 'previous-install' } catch { }
}
if ($courier) {
  Add-Candidate (Join-Path $AppData 'MultiProxy Courier') 'default'
  Add-Candidate (Join-Path $LocalAppData 'MultiProxy Courier') 'older-script'
  Add-Candidate (Join-Path $LocalAppData 'Programs\MultiProxy Courier') 'older-installer'
} else {
  Add-Candidate (Join-Path $AppData 'SkyrimNet MultiProxy') 'default'
}
if ($PreviousInstall) { Add-Candidate $PreviousInstall 'previous-install-folder' }      # a MultiProxy that kept its settings next to the program
$olderFolders = @()
foreach ($d in @(($runCommand | ForEach-Object { Folder-OfFile $_ }), (Parent-Of $shortcutTarget), $shortcutDir, $noteInstall) + @($candidates | ForEach-Object { $_.dir })) {
  if ($d -and (Is-OlderScriptCourier $d) -and ($olderFolders -inotcontains $d)) { $olderFolders += $d }
}
foreach ($d in $olderFolders) { Add-Candidate $d 'older-install' }

# ---- which settings folder -------------------------------------------------------------------------------------------------------
$found = @()      # candidates whose settings file exists: @{ dir; why; cfg; time; paired }
foreach ($c in $candidates) {
  if (-not (Test-Path -LiteralPath $c.dir -PathType Container)) { continue }
  if (-not (Has-SettingsFile $c.dir)) { continue }
  $cfg = Read-Settings $c.dir
  $t = try { (Get-Item -LiteralPath (Join-Path $c.dir $settingsFile)).LastWriteTimeUtc } catch { [datetime]::MinValue }
  $paired = ($courier -and $cfg -and $cfg.paired -eq $true)
  $found += @{ dir = $c.dir; why = $c.why; cfg = $cfg; time = $t; paired = $paired }
}
$pairedFolders = @($found | Where-Object { $_.paired })
$pickedFolder = ''; $pickedWhy = ''; $pickedCfg = $null; $pairedDir = ''; $pairNote = ''
if ($pairedFolders.Count -gt 0) {
  $best = $pairedFolders | Sort-Object { $_.time } -Descending | Select-Object -First 1      # the newest pairing; a tie keeps the order above
  $pickedFolder = $best.dir; $pickedWhy = 'paired'; $pickedCfg = $best.cfg; $pairedDir = $best.dir
  if ($pairedFolders.Count -gt 1) { $pairNote = $best.dir }
} elseif ($found.Count -gt 0) {
  $pickedFolder = $found[0].dir; $pickedWhy = 'settings'; $pickedCfg = $found[0].cfg
} elseif ($noteSettings) {
  $pickedFolder = $noteSettings; $pickedWhy = 'note'
} else {
  $pickedFolder = if ($DefaultSettings) { $DefaultSettings } else { $candidates[0].dir }; $pickedWhy = 'default'
}

# ---- models folder: the settings file first, then the note ---------------------------------------------------------------------------
$models = ''; $modelsWhy = 'none'
$fromFile = ''
try { if ($pickedCfg -and $pickedCfg.local_models) { $fromFile = Clean $pickedCfg.local_models.folder } } catch { }
if ($fromFile) { $models = $fromFile; $modelsWhy = 'settings' }
elseif ($noteModels) { $models = $noteModels; $modelsWhy = 'note' }

# ---- install folder: the note, the installer's own memory, the older install's folder, the default -----------------------------------------
$install = ''; $installWhy = 'default'
$older = if ($olderFolders.Count -gt 0) { $olderFolders[0] } else { '' }
if ($noteInstall) { $install = $noteInstall; $installWhy = 'note' }
elseif ($PreviousInstall -and $DefaultInstall -and ($PreviousInstall.TrimEnd('\') -ine $DefaultInstall.TrimEnd('\'))) { $install = $PreviousInstall; $installWhy = 'previous' }
elseif ($older) { $install = $older; $installWhy = 'older-install' }
else { $install = if ($DefaultInstall) { $DefaultInstall } else { $PreviousInstall }; $installWhy = 'default' }

# ---- the choices ---------------------------------------------------------------------------------------------------------------------------
$evidence = ($found.Count -gt 0) -or [bool]$runCommand -or [bool]$shortcutTarget -or [bool]$noteInstall -or ($olderFolders.Count -gt 0)
$win = '1'; $winWhy = 'default'
$fileWin = $null
try { if ($pickedCfg -and ($pickedCfg.PSObject.Properties.Name -contains 'start_with_windows')) { $fileWin = [bool]$pickedCfg.start_with_windows } } catch { }
if ($courier -and $null -ne $fileWin) { $win = if ($fileWin) { '1' } else { '0' }; $winWhy = 'settings' }
elseif ($noteWin -eq '0' -or $noteWin -eq '1') { $win = $noteWin; $winWhy = 'note' }
elseif ($runCommand) { $win = '1'; $winWhy = 'detected' }
elseif ($evidence) { $win = '0'; $winWhy = 'detected' }
$menu = '1'; $menuWhy = 'default'
if ($noteMenu -eq '0' -or $noteMenu -eq '1') { $menu = $noteMenu; $menuWhy = 'note' }
elseif ($shortcutTarget) { $menu = '1'; $menuWhy = 'detected' }
elseif ($evidence) { $menu = '0'; $menuWhy = 'detected' }

function Missing([string]$path) { if (-not $path) { return '0' }; if (Test-Path -LiteralPath $path -PathType Container) { return '0' } else { return '1' } }

$lines = @(
  "install=$install", "installSource=$installWhy", "staleInstall=$(if ($installWhy -in 'note','previous') { Missing $install } else { '0' })",
  "settings=$pickedFolder", "settingsSource=$pickedWhy", "staleSettings=$(if ($pickedWhy -eq 'note') { Missing $pickedFolder } else { '0' })",
  "models=$models", "modelsSource=$modelsWhy", "staleModels=$(Missing $models)",
  "startWithWindows=$win", "startWithWindowsSource=$winWhy",
  "startMenu=$menu", "startMenuSource=$menuWhy",
  "pairedFolder=$pairedDir", "newestOfSeveral=$pairNote",
  "evidence=$(if ($evidence) { '1' } else { '0' })"
)
if ($Out) { [IO.File]::WriteAllLines($Out, [string[]]$lines, (New-Object System.Text.UTF8Encoding($true))) } else { $lines }
