; MultiProxy Courier, the Windows installer (2026-10-02): the small program for your OTHER devices. Built by installer\build.py.
; Per-user (no admin) except the one firewall question, which Windows itself asks permission for. Same shape as the MultiProxy installer, minus Skyrim.

#ifndef AppVersion
  #error Run installer\build.py (it supplies AppVersion, StageDir, RuntimeDir and OutputDir).
#endif
#ifndef OlderProgramFiles
  #error Run installer\build.py (it supplies OlderProgramFiles from courier\courier-files.json).
#endif
#define AppName "MultiProxy Courier"
; The small start program (installer\launcher): Windows' Startup list names an item from its program file, so it says "MultiProxy Courier", not "Python".
#define StartExe "MultiProxy Courier.exe"
#define CourierPort "8110"
#define DiscoveryPort "8099"

[Setup]
AppId={{2E0B1236-BECF-4DE3-A240-EAFDFE8EA7DF}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=awesmdiver
AppPublisherURL=https://github.com/awesmdiver/skyrimnet-multiproxy
DefaultDirName={localappdata}\Programs\{#AppName}
UsePreviousAppDir=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
PrivilegesRequired=lowest
WizardStyle=modern dynamic
Compression=lzma2/normal
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\courier.ico
SetupIconFile=icons\courier.ico
UninstallFilesDir={app}\uninstall
CloseApplications=no
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupWindowTitle=MultiProxy Courier Setup
FinishedHeadingLabel=All set
FinishedLabelNoIcons=This device is now a MultiProxy Courier. A window with your code is open. Leave this device running, go to MultiProxy on your main device, click Other devices, enter the code and click Pair.
FinishedLabel=This device is now a MultiProxy Courier. A window with your code is open. Leave this device running, go to MultiProxy on your main device, click Other devices, enter the code and click Pair.
ClickFinish=

[Files]
Source: "{#RuntimeDir}\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; what the folder page pre-fills (read before anything is installed)
Source: "prefill.ps1"; DestDir: "{tmp}"; Flags: dontcopy

[InstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\settings-location.txt"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#StartExe}"; Parameters: "{code:RunArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\courier.ico"; Comment: "Start the MultiProxy Courier"; Check: WantStartMenu

[Run]
Filename: "{app}\python\python.exe"; Parameters: "{code:SeedArgs}"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Setting up..."
Filename: "{app}\python\python.exe"; Parameters: "{code:CodeArgs}"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Getting ready..."
Filename: "{app}\{#StartExe}"; Parameters: "{code:RunArgs}"; WorkingDir: "{app}"; Flags: nowait; StatusMsg: "Starting the Courier..."

#include "common.iss"
#include "uninstall_page.iss"

[Code]
var
  FolderInputPage: TInputDirWizardPage;
  ChoicesPage: TWizardPage;
  ChkWindows, ChkFirewall, ChkStartMenu: TNewCheckBox;
  FirewallResult: String;
  GoneNote, LostPairingNote, SeveralNote: TNewStaticText;
  PairedFolder: String;

function WantStartMenu: Boolean; begin Result := ChkStartMenu.Checked; end;

function CourierPortText: String;
var
  N: Integer;
begin
  { /COURIERPORT=N for a scripted install on another port. It ends up in a command run with administrator rights, so it is only ever a plain number from 1 to 65535. }
  N := StrToIntDef(Trim(ExpandConstant('{param:COURIERPORT|{#CourierPort}}')), 0);
  if (N < 1) or (N > 65535) then
    N := {#CourierPort};
  Result := IntToStr(N);
end;

{ Reading the firewall needs no administrator. The "{#AppName}" rules count as already in place only when there are exactly two and they are the right ones:
  enabled, inbound, allow, private networks, from this network only (RemoteIP LocalSubnet), TCP on this Courier's port and UDP on the discovery port. Anything else (a changed port, a missing rule,
  duplicates, a Windows whose netsh prints other words) is "not in place", so the tick box starts ticked as before and the old rules are replaced by exactly two. }
function RuleField(const Block, Name: String): String;
var
  P: Integer;
  Rest: String;
begin
  Result := '';
  P := Pos(Name + ':', Block);
  if P = 0 then
    exit;
  Rest := Copy(Block, P + Length(Name) + 1, Length(Block));
  P := Pos(#10, Rest);
  if P > 0 then
    Rest := Copy(Rest, 1, P - 1);
  Result := Trim(Rest);
end;

function FirewallInPlace: Boolean;
var
  Code, P, Q, Blocks: Integer;
  Show, Rest, Block, Proto, Port: String;
  Tcp, Udp, Bad: Boolean;
begin
  Show := ExpandConstant('{tmp}') + '\fw-check.txt';
  DeleteFile(Show);
  Exec(ExpandConstant('{cmd}'), '/C netsh advfirewall firewall show rule name="{#AppName}" > "' + Show + '" 2>&1', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Rest := TextFromFile(Show);
  Log('Firewall check output (' + IntToStr(Length(Rest)) + ' characters): ' + Copy(Rest, 1, 120));
  Blocks := 0;
  Tcp := False;
  Udp := False;
  Bad := False;
  repeat
    P := Pos('Rule Name:', Rest);
    if P = 0 then
      break;
    Rest := Copy(Rest, P + 10, Length(Rest));
    Block := Rest;
    Q := Pos('Rule Name:', Block);
    if Q > 0 then
      Block := Copy(Block, 1, Q - 1);
    Blocks := Blocks + 1;
    Proto := RuleField(Block, 'Protocol');
    Port := RuleField(Block, 'LocalPort');
    Log('Firewall rule found: enabled=' + RuleField(Block, 'Enabled') + ' direction=' + RuleField(Block, 'Direction') + ' profiles=' + RuleField(Block, 'Profiles') + ' action=' + RuleField(Block, 'Action') + ' protocol=' + Proto + ' port=' + Port);
    if (CompareText(RuleField(Block, 'Enabled'), 'Yes') <> 0) or (CompareText(RuleField(Block, 'Direction'), 'In') <> 0) or
       (CompareText(RuleField(Block, 'Action'), 'Allow') <> 0) or (CompareText(RuleField(Block, 'Profiles'), 'Private') <> 0) or
       (Pos('LocalSubnet', RuleField(Block, 'RemoteIP')) <> 1) then
      Bad := True
    else if (CompareText(Proto, 'TCP') = 0) and (Port = CourierPortText) then
      Tcp := True
    else if (CompareText(Proto, 'UDP') = 0) and (Port = '{#DiscoveryPort}') then
      Udp := True
    else
      Bad := True;
  until False;
  Result := (Blocks = 2) and Tcp and Udp and (not Bad);
  if Result then
    Log('Firewall rules already in place: the tick box starts unticked')
  else
    Log('Firewall rules not in place (' + IntToStr(Blocks) + ' rule(s) found, expected port ' + CourierPortText + '): the tick box starts ticked');
end;

function SettingsFolder: String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(FolderInputPage.Values[1]));
end;

function ModelsFolder: String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(FolderInputPage.Values[2]));
end;

{ Does this folder hold a Courier's settings that are paired with a main device? (the same test the Courier uses: "paired": true in courier.json) }
function FolderHasPairing(const Dir: String): Boolean;
var
  Text: AnsiString;
begin
  Result := False;
  if (Dir <> '') and LoadStringFromFile(Dir + '\courier.json', Text) then
    Result := (Pos('"paired": true', String(Text)) > 0) or (Pos('"paired":true', String(Text)) > 0);
end;

{ An update that would start from a settings folder with no pairing leaves the main device unpaired: said on the folder page, before Install. }
procedure ShowFolderNotes;
var
  Lines: String;
begin
  Lines := StaleFolderLines(True, True, True);
  if (PairedFolder <> '') and (not FolderHasPairing(SettingsFolder)) then
  begin
    if Lines <> '' then Lines := Lines + #13#10;
    Lines := Lines + 'This looks like an update, but the settings folder you chose has no pairing. You will need to pair your main device again.';
  end;
  GoneNote.Caption := Lines;
end;

procedure SettingsFolderChanged(Sender: TObject);
begin
  ShowFolderNotes;
end;

function RunArgs(Param: String): String;
begin
  Result := 'run --config-dir ' + Quoted(SettingsFolder) + ' --show-ready-window ' + ExpandConstant('{param:COURIERARGS|}');
end;

function SeedArgs(Param: String): String;
begin
  Result := Quoted(ExpandConstant('{app}') + '\installer_helper.py') + ' seed-courier --program-dir ' + Quoted(ExpandConstant('{app}')) + ' --config-dir ' + Quoted(SettingsFolder) + ' --port ' + CourierPortText;
  if ModelsFolder <> '' then
    Result := Result + ' --models-folder ' + Quoted(ModelsFolder);
end;

function CodeArgs(Param: String): String;
var
  Win: String;
begin
  if ChkWindows.Checked then Win := 'on' else Win := 'off';
  if FirewallResult = '' then FirewallResult := 'unknown';
  Result := Quoted(ExpandConstant('{app}') + '\courier.py') + ' code --config-dir ' + Quoted(SettingsFolder) + ' --start-with-windows ' + Win + ' --firewall ' + FirewallResult;
end;

procedure InitializeWizard;
var
  Old: String;
begin
  FolderInputPage := CreateInputDirPage(wpWelcome, 'Where should the Courier be installed?', '', '', False, '');
  FolderInputPage.Add('Install folder');
  FolderInputPage.Add('Settings folder');
  FolderInputPage.Add('Where to save local AI models (these are big; you can choose later)');
  FolderInputPage.Values[0] := WizardDirValue;
  Old := ExpandConstant('{userappdata}') + '\{#AppName}';           { where earlier Couriers kept their settings: reused, so this device stays paired }
  FolderInputPage.Values[1] := Old;
  FolderInputPage.Values[2] := '';
  SeveralNote := TNewStaticText.Create(FolderInputPage);
  SeveralNote.Parent := FolderInputPage.Surface;
  SeveralNote.Anchors := [akLeft, akRight, akBottom];
  SeveralNote.Left := 0;
  SeveralNote.Width := FolderInputPage.SurfaceWidth;
  SeveralNote.AutoSize := False;
  SeveralNote.WordWrap := True;
  SeveralNote.Height := ScaleY(18);
  SeveralNote.Top := FolderInputPage.SurfaceHeight - ScaleY(78);
  SeveralNote.Font.Color := HintColor;
  GoneNote := TNewStaticText.Create(FolderInputPage);
  GoneNote.Parent := FolderInputPage.Surface;
  GoneNote.Anchors := [akLeft, akRight, akBottom];
  GoneNote.Left := 0;
  GoneNote.Width := FolderInputPage.SurfaceWidth;
  GoneNote.AutoSize := False;
  GoneNote.WordWrap := True;
  GoneNote.Height := ScaleY(56);
  GoneNote.Top := FolderInputPage.SurfaceHeight - ScaleY(58);
  GoneNote.Font.Color := WarnColor;

  ChoicesPage := CreateCustomPage(FolderInputPage.ID, 'A few choices', '');
  ChkWindows := TNewCheckBox.Create(ChoicesPage);
  ChkWindows.Parent := ChoicesPage.Surface;
  ChkWindows.Caption := 'Start the Courier when Windows starts';
  ChkWindows.Checked := True;
  ChkWindows.Width := ChoicesPage.SurfaceWidth;
  ChkWindows.Top := 0;
  ChkWindows.Height := CheckHeight;
  ChkStartMenu := TNewCheckBox.Create(ChoicesPage);
  ChkStartMenu.Parent := ChoicesPage.Surface;
  ChkStartMenu.Caption := 'Add it to the Start menu';
  ChkStartMenu.Checked := True;
  ChkStartMenu.Width := ChoicesPage.SurfaceWidth;
  ChkStartMenu.Top := ScaleY(26);
  ChkStartMenu.Height := CheckHeight;
  ChkFirewall := TNewCheckBox.Create(ChoicesPage);
  ChkFirewall.Parent := ChoicesPage.Surface;
  ChkFirewall.Caption := 'Let my main device reach this one';
  ChkFirewall.Checked := not FirewallInPlace;       { already allowed (an update, or a re-install): nothing to ask, nothing elevated }
  ChkFirewall.Width := ChoicesPage.SurfaceWidth;
  ChkFirewall.Top := ScaleY(52);
  ChkFirewall.Height := CheckHeight;
  with TNewStaticText.Create(ChoicesPage) do
  begin
    Parent := ChoicesPage.Surface;
    Left := ScaleX(18);
    Top := ScaleY(76);
    Width := ChoicesPage.SurfaceWidth - ScaleX(18);
    AutoSize := False;
    WordWrap := True;
    Height := ScaleY(48);
    if ChkFirewall.Checked then
      Caption := 'Windows will ask for permission in a pop-up. Click Yes so your main device can find this Courier.'
    else
      Caption := 'Already allowed.';
  end;

  { An update starts from what is already on this device, not from the defaults (prefill.ps1, in this order for each value: the earlier settings file, the note the last install
    left in the registry, the older install itself, the defaults). A paired Courier's settings folder is found wherever an earlier Courier kept it, so an update stays paired.
    The person can still change every answer, and the switches below win over all of it. }
  RunPrefill('courier', ExpandConstant('{localappdata}') + '\Programs\{#AppName}', WizardDirValue, Old);
  if PrefillValue('install') <> '' then FolderInputPage.Values[0] := PrefillValue('install');
  if PrefillValue('settings') <> '' then FolderInputPage.Values[1] := PrefillValue('settings');
  FolderInputPage.Values[2] := PrefillValue('models');
  ChkWindows.Checked := PrefillValue('startWithWindows') <> '0';
  ChkStartMenu.Checked := PrefillValue('startMenu') <> '0';
  PairedFolder := PrefillValue('pairedFolder');
  if PrefillValue('newestOfSeveral') <> '' then
    SeveralNote.Caption := 'We found more than one older Courier. Here are the settings for the newest one.';
  FolderInputPage.Edits[1].OnChange := @SettingsFolderChanged;

  { Scripted installs (and tests): /DIR= /SETTINGSDIR= /MODELSDIR= /STARTWITHWINDOWS=0|1 /STARTMENU=0|1 /FIREWALL=0|1 }
  if ExpandConstant('{param:SETTINGSDIR|}') <> '' then FolderInputPage.Values[1] := ExpandConstant('{param:SETTINGSDIR|}');
  if ExpandConstant('{param:MODELSDIR|}') <> '' then FolderInputPage.Values[2] := ExpandConstant('{param:MODELSDIR|}');
  if ExpandConstant('{param:STARTWITHWINDOWS|}') <> '' then ChkWindows.Checked := ExpandConstant('{param:STARTWITHWINDOWS|}') = '1';
  if ExpandConstant('{param:STARTMENU|}') <> '' then ChkStartMenu.Checked := ExpandConstant('{param:STARTMENU|}') = '1';
  if ExpandConstant('{param:FIREWALL|}') <> '' then ChkFirewall.Checked := ExpandConstant('{param:FIREWALL|}') = '1';
  ShowFolderNotes;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = FolderInputPage.ID then
  begin
    if (Trim(FolderInputPage.Values[0]) = '') or (SettingsFolder = '') then
    begin
      MsgBox('Choose the install folder and the settings folder.', mbError, MB_OK);
      Result := False;
      exit;
    end;
    WizardForm.DirEdit.Text := RemoveBackslashUnlessRoot(Trim(FolderInputPage.Values[0]));
  end;
end;

{ The name of the main device this Courier is already paired with (an update keeps its pairing), or '' when none: asked of the Courier itself, so the last page never promises a code that isn't shown. }
function PairedWith: String;
var
  Code: Integer;
  Show: String;
begin
  Show := ExpandConstant('{tmp}') + '\paired.txt';
  DeleteFile(Show);
  Exec(ExpandConstant('{cmd}'), '/C ""' + ExpandConstant('{app}') + '\python\python.exe" ' + Quoted(ExpandConstant('{app}') + '\courier.py') + ' paired --config-dir ' + Quoted(SettingsFolder) + ' > "' + Show + '""', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := Trim(TextFromFile(Show));
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Paired: String;
begin
  if CurPageID = ChoicesPage.ID then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedHeadingLabel.Caption := 'All set ' + #$D83C#$DF89;
    Paired := PairedWith;
    if Paired <> '' then
    begin
      WizardForm.FinishedLabel.Caption := 'You''re all set, and this device is already paired with ' + Paired + '. There is nothing more to do here, so just leave this computer running and head back to your main device.';
      WizardForm.FinishedLabel.Update;
    end;
  end;
end;

{ An older Courier (the zip-file setup, 2026-10-02): its program AND its settings share one folder, the program inside a ".venv" there. This installer puts the program elsewhere and only
  reuses that folder's settings, so without this the older one would keep running beside the new one (same port) and be left behind by an uninstall. }
const
  OlderProgramFiles = '{#OlderProgramFiles}';          { the Courier's one file list (courier/courier-files.json), given by installer\build.py }

function IsOlderCourier(const Dir: String): Boolean;
begin
  Result := (Dir <> '') and FileExists(Dir + '\courier.py') and (DirExists(Dir + '\.venv') or FileExists(Dir + '\Start Courier.bat'));
end;

procedure RemoveOlderProgramFiles(const Dir: String);
var
  Rest, Name: String;
  P: Integer;
begin
  Rest := OlderProgramFiles + ',';
  repeat
    P := Pos(',', Rest);
    if P = 0 then
      break;
    Name := Copy(Rest, 1, P - 1);
    Rest := Copy(Rest, P + 1, Length(Rest));
    if Name <> '' then
      DeleteFile(Dir + '\' + Name);
  until False;
  DelTree(Dir + '\.venv', True, True, True);
  DelTree(Dir + '\__pycache__', True, True, True);
end;

{ Stops it, takes its start-with-Windows entry and Start menu shortcut away (this install adds its own if ticked) and removes its program files. courier.json (the pairing code) and everything else stays. }
procedure TakeOverOlderCourier(const Dir: String);
begin
  Log('Found an older Courier in ' + Dir + ': taking it over.');
  StopProcessesUnder(Dir + '\.venv');
  Log('  stopped whatever was running from ' + Dir + '\.venv');
  if RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}') then
    Log('  removed its start-with-Windows entry');
  if DeleteFile(ExpandConstant('{autoprograms}') + '\{#AppName}.lnk') then
    Log('  removed its Start menu shortcut');
  RemoveOlderProgramFiles(Dir);
  Log('  removed its program files; its settings and pairing code stay in ' + Dir);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Where: array[0..2] of String;
  I, J: Integer;
  Seen: Boolean;
begin
  Result := '';
  StopRunningFrom(ExpandConstant('{app}'), '');          { an update: close the running Courier first; its settings and pairing code are in the settings folder }
  Where[0] := ExpandConstant('{userappdata}') + '\{#AppName}';
  Where[1] := ExpandConstant('{localappdata}') + '\{#AppName}';
  if ExpandConstant('{param:OLDERDIR|}') <> '' then      { tests only: /OLDERDIR=<folder> looks there instead of this device's real folders }
  begin
    Where[0] := ExpandConstant('{param:OLDERDIR|}');
    Where[1] := Where[0];
  end;
  Where[2] := SettingsFolder;
  for I := 0 to 2 do
  begin
    Seen := False;
    for J := 0 to I - 1 do
      if CompareText(Where[I], Where[J]) = 0 then
        Seen := True;
    if (not Seen) and IsOlderCourier(Where[I]) then
      TakeOverOlderCourier(Where[I]);
  end;
end;

{ The firewall rule, private networks only (the same two rules the zip-file setup adds): private networks only, from this network only (LocalSubnet), and only for this Courier's own program. The commands
  go into a small batch file that Windows runs with administrator rights (it asks permission once): no PowerShell, so nothing about the folder's name can be read as
  a command. A percent sign in the path is doubled for the batch file. }
function FirewallScriptText: String;
var
  Prog, Tail: String;
begin
  Prog := ExpandConstant('{app}') + '\python\pythonw.exe';
  StringChangeEx(Prog, '%', '%%', True);
  Tail := ' dir=in action=allow profile=private remoteip=LocalSubnet';
  Result := '@echo off' + #13#10 +
            'netsh advfirewall firewall delete rule name="{#AppName}"' + #13#10 +
            'netsh advfirewall firewall add rule name="{#AppName}"' + Tail + ' protocol=TCP localport=' + CourierPortText + ' program="' + Prog + '"' + #13#10 +
            'netsh advfirewall firewall add rule name="{#AppName}"' + Tail + ' protocol=UDP localport={#DiscoveryPort} program="' + Prog + '"' + #13#10;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Script, Show: String;
begin
  if CurStep = ssDone then
    UseThePageFromTheAppsList('{2E0B1236-BECF-4DE3-A240-EAFDFE8EA7DF}');
  if CurStep = ssPostInstall then
    WritePrefillNote('courier', ExpandConstant('{app}'), SettingsFolder, ModelsFolder, ChkWindows.Checked, ChkStartMenu.Checked);
  if (CurStep = ssInstall) and ChkFirewall.Checked then
  begin
    Script := ExpandConstant('{tmp}') + '\courier-firewall.cmd';
    SaveStringToFile(Script, FirewallScriptText, False);
    ShellExec('runas', ExpandConstant('{cmd}'), '/C ""' + Script + '""', '', SW_HIDE, ewWaitUntilTerminated, Code);        { "-Verb RunAs" in the zip setup: Windows asks permission once }
    Show := ExpandConstant('{tmp}') + '\fw.txt';
    Exec(ExpandConstant('{cmd}'), '/C netsh advfirewall firewall show rule name="{#AppName}" > "' + Show + '" 2>&1', '', SW_HIDE, ewWaitUntilTerminated, Code);
    if Pos('{#AppName}', TextFromFile(Show)) > 0 then
      FirewallResult := 'added'
    else
      FirewallResult := 'declined';
  end
  else if CurStep = ssInstall then
    FirewallResult := 'declined';
end;

function ThisSettingsFolder: String;
begin
  Result := TextFromFile(ExpandConstant('{app}') + '\settings-location.txt');
  if Result = '' then
    Result := ExpandConstant('{userappdata}') + '\{#AppName}';
end;

function Ticked(const Selected, Id: String): String;
begin
  if Pos(',' + Id + ',', ',' + Selected + ',') > 0 then
    Result := '1'
  else
    Result := '0';
end;

{ Uninstall (Apps list): ONE page of tick boxes, then it just does it (Windows asks permission for the firewall rule, if that is ticked). }

var
  PageAnswer: String;                { what was ticked on the page (a silent uninstall uses the /REMOVE... switches instead) }

function InitializeUninstall: Boolean;
begin
  Result := True;
  PageAnswer := '';
  if UninstallSilent and (ExpandConstant('{param:PAGE|0}') <> '1') then
    exit;                                       { a scripted uninstall names what to remove with switches; by default it removes only the program }
  Result := AskWhatToRemove('Uninstall MultiProxy Courier', True, ExpandConstant('{app}'), ThisSettingsFolder, PageAnswer);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App, Items, Json: String;
  Code: Integer;
begin
  if CurUninstallStep <> usUninstall then
    exit;
  App := ExpandConstant('{app}');
  StopRunningFrom(App, '');
  if DirExists(ThisSettingsFolder + '\.venv\Scripts') then
    StopProcessesUnder(ThisSettingsFolder + '\.venv');      { an older Courier's program left in the settings folder: no tray icon stays behind }
  RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}');
  ForgetPrefillNote('courier');
  if ExpandConstant('{param:PAGE|0}') = '1' then Items := PageAnswer else Items := SwitchedItems(True);
  if (Items <> '') and (ThisSettingsFolder <> App) then
  begin
    Json := RunHelper(App, 'remove --courier --settings-dir ' + Quoted(ThisSettingsFolder) + ' --items ' + Items);
    ReportLeftovers(Json);
  end;
  if Pos('firewall', Items) > 0 then
  begin
    { netsh by its full path, with administrator rights. The command window is shown (it is over in a moment) rather than hidden: a hidden elevated program is how the permission
      pop-up gets lost behind other windows. The pop-up itself is Windows's own and can't be moved; so after it, the rule is looked for again and a rule that is still there is said out loud. }
    ShellExec('runas', ExpandConstant('{sys}') + '\netsh.exe', 'advfirewall firewall delete rule name="{#AppName}"', '', SW_SHOWNORMAL, ewWaitUntilTerminated, Code);
    ReportFirewallLeftover(App, '{#AppName}');
  end;
end;
