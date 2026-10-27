; SkyrimNet MultiProxy, the Windows installer (2026-10-02). Built by installer\build.py (which passes AppVersion, StageDir, RuntimeDir, ModPackage, OutputDir, OutputBase).
; Per-user (no admin). Never writes into Skyrim's folder or a mod manager: the Skyrim mod package is placed in MultiProxy's own folder, for the player to drag in.

#ifndef AppVersion
  #error Run installer\build.py (it supplies AppVersion, StageDir, RuntimeDir, ModPackage and OutputDir).
#endif
#define AppName "SkyrimNet MultiProxy"
; The small start program (installer\launcher): Windows' Startup list names an item from its program file, so it says "SkyrimNet MultiProxy", not "Python".
#define StartExe "SkyrimNet MultiProxy.exe"
#define ModPackageName ExtractFileName(ModPackage)

[Setup]
AppId={{0A43CE19-12C3-4285-A065-E2FBD58F8836}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}.0
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
UninstallDisplayIcon={app}\multiproxy.ico
SetupIconFile=icons\multiproxy.ico
UninstallFilesDir={app}\uninstall
CloseApplications=no
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupWindowTitle=SkyrimNet MultiProxy Setup
FinishedHeadingLabel=All set
FinishedLabelNoIcons=MultiProxy is ready. Leave the box ticked and press Finish to start.
FinishedLabel=MultiProxy is ready. Leave the box ticked and press Finish to start.
ClickFinish=

[Files]
; the bundled Python (python.org's embeddable build, with every dependency already in it), then the program
Source: "{#RuntimeDir}\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#ModPackage}"; DestDir: "{app}\mod"; Flags: ignoreversion
; the same package once more, so it can be offered on page 2 before anything is installed
Source: "{#ModPackage}"; DestDir: "{tmp}"; Flags: dontcopy
; what the folder page pre-fills (read before anything is installed)
Source: "prefill.ps1"; DestDir: "{tmp}"; Flags: dontcopy

[InstallDelete]
; an older install left versioned files (SkyrimNetMultiProxy-Mod-<version>.zip); the package now has one fixed name, so a new one replaces the old in place
Type: files; Name: "{app}\mod\SkyrimNetMultiProxy-Mod-*.zip"
Type: filesandordirs; Name: "{app}\__pycache__"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\mod"
Type: filesandordirs; Name: "{app}\docs"
Type: files; Name: "{app}\settings-location.txt"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#StartExe}"; WorkingDir: "{app}"; IconFilename: "{app}\multiproxy.ico"; Comment: "Start MultiProxy in the system tray"; Check: WantStartMenu
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#StartExe}"; WorkingDir: "{app}"; IconFilename: "{app}\multiproxy.ico"; Comment: "Start MultiProxy in the system tray"; Check: WantDesktop

[Registry]
; Start with Windows: the same per-user entry, with the same name and command, that MultiProxy's own Settings page and tray menu switch on and off.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#StartExe}"""; Flags: uninsdeletevalue; Check: WantStartWithWindows
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#AppName}"; Flags: deletevalue; Check: not WantStartWithWindows

[Run]
Filename: "{app}\python\python.exe"; Parameters: "{code:SeedArgs}"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Creating the settings folder..."
Filename: "{app}\{#StartExe}"; WorkingDir: "{app}"; Flags: nowait; StatusMsg: "Starting MultiProxy..."
Filename: "{code:DashboardUrl}"; Description: "Open the dashboard now"; Flags: postinstall shellexec skipifsilent nowait

#include "common.iss"
#include "uninstall_page.iss"

[Code]
var
  FolderPage, OldPage, ChoicesPage: TWizardPage;
  OldHint: TNewStaticText;
  FolderInputPage: TInputDirWizardPage;
  SkyrimPage: TWizardPage;
  PackageEdit, FinishEdit, ModelsEdit, OldEdit: TNewEdit;
  ChkWindows, ChkStartMenu, ChkDesktop: TNewCheckBox;
  PackageCopiedTo: String;
  FinishBuilt, Installed: Boolean;
  GoneNote: TNewStaticText;

function WantStartWithWindows: Boolean; begin Result := ChkWindows.Checked; end;
function WantStartMenu: Boolean; begin Result := ChkStartMenu.Checked; end;
function WantDesktop: Boolean; begin Result := ChkDesktop.Checked; end;

function AppFolder: String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(FolderInputPage.Values[0]));
end;

function SettingsFolder: String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(FolderInputPage.Values[1]));
end;

function PackagePath: String;
begin
  Result := AppFolder + '\mod\{#ModPackageName}';
end;

function SeedArgs(Param: String): String;
begin
  Result := Quoted(ExpandConstant('{app}') + '\installer_helper.py') + ' seed-multiproxy --program-dir ' + Quoted(ExpandConstant('{app}')) +
            ' --settings-dir ' + Quoted(SettingsFolder);
  if Trim(ModelsEdit.Text) <> '' then
    Result := Result + ' --models-folder ' + Quoted(Trim(ModelsEdit.Text));
  if Trim(OldEdit.Text) <> '' then
    Result := Result + ' --import-from ' + Quoted(Trim(OldEdit.Text));
end;

function DashboardUrl(Param: String): String;
var
  Port: Int64;
  Cfg: String;
begin
  Port := 8000;
  Cfg := TextFromFile(SettingsFolder + '\config.json');
  if Cfg <> '' then
    if JsonNumber(Cfg, 'main_port') > 0 then
      Port := JsonNumber(Cfg, 'main_port');
  Result := 'http://127.0.0.1:' + IntToStr(Port) + '/';
end;

{ ---- the mod package, placed in MultiProxy's own folder (never Skyrim's), so Open folder works before and after Install ---- }
procedure PlacePackage;
begin
  if (PackageCopiedTo <> '') and (PackageCopiedTo <> PackagePath) then
  begin
    DeleteFile(PackageCopiedTo);
    RemoveDir(ExtractFilePath(PackageCopiedTo));
    PackageCopiedTo := '';
  end;
  if FileExists(PackagePath) then
    exit;
  if ForceDirectories(ExtractFilePath(PackagePath)) then
  begin
    ExtractTemporaryFile('{#ModPackageName}');
    if FileCopy(ExpandConstant('{tmp}') + '\{#ModPackageName}', PackagePath, False) then
      PackageCopiedTo := PackagePath;
  end;
end;

procedure OpenPackageFolderClick(Sender: TObject);
begin
  if Installed then
    OpenFolderSelecting(ExpandConstant('{app}') + '\mod\{#ModPackageName}')
  else
    OpenFolderSelecting(PackagePath);
end;

procedure CopyPackageLocationClick(Sender: TObject);
begin
  if Installed then
    CopyToClipboard(ExpandConstant('{app}') + '\mod\{#ModPackageName}')
  else
    CopyToClipboard(PackagePath);
end;

procedure BrowseModelsClick(Sender: TObject);
var
  Dir: String;
begin
  Dir := ModelsEdit.Text;
  if BrowseForFolder('Where should local AI models be saved?', Dir, True) then
    ModelsEdit.Text := Dir;
end;

procedure BrowseOldClick(Sender: TObject);
var
  Dir: String;
begin
  Dir := OldEdit.Text;
  if BrowseForFolder('Pick your old MultiProxy folder', Dir, False) then
    OldEdit.Text := Dir;
end;

function Wrap(Parent: TWinControl; Text: String; Top, Height: Integer; Bold: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(Parent);
  Result.Parent := Parent;
  Result.Left := 0;
  Result.Top := Top;
  Result.Width := Parent.Width;
  Result.AutoSize := False;
  Result.WordWrap := True;
  Result.Height := Height;
  Result.Caption := Text;
  if Bold then
    Result.Font.Style := [fsBold];
end;

function MakeButton(Parent: TWinControl; Text: String; Left, Top, Width: Integer; Handler: TNotifyEvent): TNewButton;
begin
  Result := TNewButton.Create(Parent);
  Result.Parent := Parent;
  Result.Left := Left;
  Result.Top := Top;
  Result.Width := Width;
  Result.Height := ScaleY(23);
  Result.Caption := Text;
  Result.OnClick := Handler;
end;

procedure BuildPackageRow(Parent: TWinControl; var Edit: TNewEdit; Top: Integer);
begin
  Edit := TNewEdit.Create(Parent);
  Edit.Parent := Parent;
  Edit.Left := 0;
  Edit.Top := Top;
  Edit.Width := Parent.Width - ScaleX(215);
  Edit.ReadOnly := True;
  MakeButton(Parent, 'Open folder', Parent.Width - ScaleX(205), Top - 1, ScaleX(95), @OpenPackageFolderClick);
  MakeButton(Parent, 'Copy location', Parent.Width - ScaleX(105), Top - 1, ScaleX(105), @CopyPackageLocationClick);
end;

procedure InitializeWizard;
var
  PrevSettings, Old: String;
  Top: Integer;
begin
  { Step 1: the two folders }
  FolderInputPage := CreateInputDirPage(wpWelcome, 'Where should MultiProxy be installed?', '', '', False, '');
  FolderInputPage.Add('Program folder');
  FolderInputPage.Add('Settings folder');
  FolderInputPage.Values[0] := WizardDirValue;
  PrevSettings := TextFromFile(WizardDirValue + '\settings-location.txt');
  if PrevSettings = '' then
    PrevSettings := ExpandConstant('{userappdata}') + '\{#AppName}';
  FolderInputPage.Values[1] := PrevSettings;
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

  { Optional, only when there are no settings in the chosen settings folder yet: bring an older zip-style install's settings along. }
  OldPage := CreateCustomPage(FolderInputPage.ID, 'Bring your settings along?', 'Optional');
  Wrap(OldPage.Surface, 'If you used the zipped version of MultiProxy before, pick that folder to keep your keys and settings. ' +
       'Otherwise, leave this empty and press Next.', 0, ScaleY(48), False);
  OldEdit := TNewEdit.Create(OldPage);
  OldEdit.Parent := OldPage.Surface;
  OldEdit.Left := 0;
  OldEdit.Top := ScaleY(56);
  OldEdit.Width := OldPage.SurfaceWidth - ScaleX(95);
  MakeButton(OldPage.Surface, 'Browse...', OldPage.SurfaceWidth - ScaleX(85), ScaleY(55), ScaleX(85), @BrowseOldClick);
  OldHint := Wrap(OldPage.Surface, 'Pick the folder that has your old MultiProxy files.', ScaleY(90), ScaleY(48), False);
  OldHint.Font.Color := HintColor;

  { Step 2: connect it to Skyrim (the mod package, for the mod manager) }
  SkyrimPage := CreateCustomPage(OldPage.ID, 'Connect it to Skyrim', '');
  Top := 0;
  Wrap(SkyrimPage.Surface, 'Skyrim connects to MultiProxy using a small mod.', Top, ScaleY(18), False);
  Top := Top + ScaleY(22);
  Wrap(SkyrimPage.Surface, 'Install it with Vortex or Mod Organizer 2 so it acts like your other mods.', Top, ScaleY(32), False);
  Top := Top + ScaleY(36);
  Wrap(SkyrimPage.Surface, 'The mod package (included)', Top, ScaleY(16), True);
  Top := Top + ScaleY(20);
  BuildPackageRow(SkyrimPage.Surface, PackageEdit, Top);
  Top := Top + ScaleY(32);
  Wrap(SkyrimPage.Surface, 'Click ''Open folder'' and drag the mod package into Vortex or MO2 (Install from file). You can also do this later from Settings.', Top, ScaleY(48), False);

  { Step 3: a few choices }
  ChoicesPage := CreateCustomPage(SkyrimPage.ID, 'A few choices', '');
  ChkWindows := TNewCheckBox.Create(ChoicesPage);
  ChkWindows.Parent := ChoicesPage.Surface;
  ChkWindows.Caption := 'Start MultiProxy when Windows starts';
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
  ChkDesktop := TNewCheckBox.Create(ChoicesPage);
  ChkDesktop.Parent := ChoicesPage.Surface;
  ChkDesktop.Caption := 'Add a desktop shortcut';
  ChkDesktop.Checked := False;
  ChkDesktop.Width := ChoicesPage.SurfaceWidth;
  ChkDesktop.Top := ScaleY(52);
  ChkDesktop.Height := CheckHeight;
  Wrap(ChoicesPage.Surface, 'Where to save local AI models (these are big; you can choose later)', ScaleY(96), ScaleY(16), True);
  ModelsEdit := TNewEdit.Create(ChoicesPage);
  ModelsEdit.Parent := ChoicesPage.Surface;
  ModelsEdit.Left := 0;
  ModelsEdit.Top := ScaleY(116);
  ModelsEdit.Width := ChoicesPage.SurfaceWidth - ScaleX(95);
  MakeButton(ChoicesPage.Surface, 'Browse...', ChoicesPage.SurfaceWidth - ScaleX(85), ScaleY(115), ScaleX(85), @BrowseModelsClick);
  Wrap(ChoicesPage.Surface, 'Leave this blank to decide when you download your first model.', ScaleY(142), ScaleY(16), False);

  { An update starts from what is already on this device, not from the defaults (prefill.ps1, in this order for each value: the earlier settings file, the note the last install
    left in the registry, the earlier install itself, the defaults). The person can still change every answer, and the switches below win over all of it. }
  RunPrefill('multiproxy', ExpandConstant('{localappdata}') + '\Programs\{#AppName}', WizardDirValue, ExpandConstant('{userappdata}') + '\{#AppName}');
  if PrefillValue('install') <> '' then FolderInputPage.Values[0] := PrefillValue('install');
  if PrefillValue('settings') <> '' then FolderInputPage.Values[1] := PrefillValue('settings');
  ModelsEdit.Text := PrefillValue('models');
  ChkWindows.Checked := PrefillValue('startWithWindows') <> '0';
  ChkStartMenu.Checked := PrefillValue('startMenu') <> '0';
  GoneNote.Caption := StaleFolderLines(True, True, True);

  { Scripted installs (and tests): every answer can be given on the command line, e.g.  Setup.exe /VERYSILENT /DIR="D:\MultiProxy" /SETTINGSDIR="D:\MultiProxy settings"
    /MODELSDIR="E:\AI models" /STARTWITHWINDOWS=0 /STARTMENU=0 /DESKTOPICON=1 /OLDINSTALL="C:\old\MultiProxy" }
  if ExpandConstant('{param:SETTINGSDIR|}') <> '' then FolderInputPage.Values[1] := ExpandConstant('{param:SETTINGSDIR|}');
  if ExpandConstant('{param:MODELSDIR|}') <> '' then ModelsEdit.Text := ExpandConstant('{param:MODELSDIR|}');
  if ExpandConstant('{param:OLDINSTALL|}') <> '' then OldEdit.Text := ExpandConstant('{param:OLDINSTALL|}');
  if ExpandConstant('{param:STARTWITHWINDOWS|}') <> '' then ChkWindows.Checked := ExpandConstant('{param:STARTWITHWINDOWS|}') = '1';
  if ExpandConstant('{param:STARTMENU|}') <> '' then ChkStartMenu.Checked := ExpandConstant('{param:STARTMENU|}') = '1';
  if ExpandConstant('{param:DESKTOPICON|}') <> '' then ChkDesktop.Checked := ExpandConstant('{param:DESKTOPICON|}') = '1';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = OldPage.ID then
    Result := FileExists(SettingsFolder + '\config.json');       { they already have settings here (an upgrade): nothing to bring }
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = FolderInputPage.ID then
  begin
    if (AppFolder = '') or (SettingsFolder = '') then
    begin
      MsgBox('Please choose both folders.', mbError, MB_OK);
      Result := False;
      exit;
    end;
    if (Length(AppFolder) < 4) or (Length(SettingsFolder) < 4) then
    begin
      MsgBox('Please choose a folder, not a whole drive.', mbError, MB_OK);
      Result := False;
      exit;
    end;
    WizardForm.DirEdit.Text := AppFolder;          // the program folder they chose is what app means from here on
  end;
  if (OldPage <> nil) and (CurPageID = OldPage.ID) and (Trim(OldEdit.Text) <> '') then
    if not FileExists(Trim(OldEdit.Text) + '\config.json') then
    begin
      MsgBox('That does not look like an old MultiProxy folder. Pick the correct folder, or leave the box empty to start fresh.', mbError, MB_OK);
      Result := False;
    end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Top: Integer;
  Lbl: TNewStaticText;
begin
  if CurPageID = SkyrimPage.ID then
  begin
    PlacePackage;
    PackageEdit.Text := PackagePath;
  end;
  if CurPageID = ChoicesPage.ID then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
  if CurPageID = wpFinished then
  begin
    Installed := True;
    if not FinishBuilt then
    begin
      FinishBuilt := True;
      WizardForm.FinishedHeadingLabel.Caption := 'All set ' + #$D83C#$DF89;
      Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(8);
      Lbl := Wrap(WizardForm.FinishedPage, 'The Skyrim mod, for your mod manager', Top, ScaleY(16), True);
      Lbl.Left := WizardForm.FinishedLabel.Left;
      Lbl.Width := WizardForm.FinishedLabel.Width;
      FinishEdit := TNewEdit.Create(WizardForm.FinishedPage);
      FinishEdit.Parent := WizardForm.FinishedPage;
      FinishEdit.Left := WizardForm.FinishedLabel.Left;
      FinishEdit.Top := Top + ScaleY(20);
      FinishEdit.Width := WizardForm.FinishedLabel.Width;
      FinishEdit.ReadOnly := True;
      FinishEdit.Text := ExpandConstant('{app}') + '\mod\{#ModPackageName}';
      MakeButton(WizardForm.FinishedPage, 'Open folder', FinishEdit.Left, FinishEdit.Top + ScaleY(28), ScaleX(95), @OpenPackageFolderClick);
      MakeButton(WizardForm.FinishedPage, 'Copy location', FinishEdit.Left + ScaleX(105), FinishEdit.Top + ScaleY(28), ScaleX(105), @CopyPackageLocationClick);
      WizardForm.RunList.Top := FinishEdit.Top + ScaleY(66);
    end;
  end;
end;

{ ---- upgrade in place: close the running tray app (and its server) first; settings, keys, pairings and models live in the settings folder and are never touched ---- }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Where: String;
begin
  Result := '';
  Where := ExpandConstant('{app}');
  StopRunningFrom(Where, Quoted(Where + '\multiproxy_tray.py') + ' quit');
  if (PackageCopiedTo <> '') and (PackageCopiedTo = Where + '\mod\{#ModPackageName}') then
    PackageCopiedTo := '';        { it is going to be installed there anyway }
end;

procedure DeinitializeSetup;
begin
  { Cancelled before installing: take back the package copy that page 2 placed, and its folder if that is all there was. }
  if (not Installed) and (PackageCopiedTo <> '') then
  begin
    DeleteFile(PackageCopiedTo);
    RemoveDir(ExtractFilePath(PackageCopiedTo));
    RemoveDir(ExtractFilePath(ExtractFilePath(PackageCopiedTo)));
  end;
end;

{ ---- uninstall (Apps list): ONE page of tick boxes, then it just does it. Skyrim's folder and mod managers are never touched. ---- }
function ThisSettingsFolder: String;
begin
  Result := TextFromFile(ExpandConstant('{app}') + '\settings-location.txt');
  if Result = '' then
    Result := ExpandConstant('{app}');
end;

function Ticked(const Selected, Id: String): String;
begin
  if Pos(',' + Id + ',', ',' + Selected + ',') > 0 then
    Result := '1'
  else
    Result := '0';
end;


var
  PageAnswer: String;                { what was ticked on the page (a silent uninstall uses the /REMOVE... switches instead) }

function InitializeUninstall: Boolean;
begin
  Result := True;
  PageAnswer := '';
  if UninstallSilent and (ExpandConstant('{param:PAGE|0}') <> '1') then
    exit;                                       { a scripted uninstall names what to remove with switches; by default it removes only the program }
  Result := AskWhatToRemove('Uninstall SkyrimNet MultiProxy', False, ExpandConstant('{app}'), ThisSettingsFolder, PageAnswer);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App, Items, Json: String;
  Code: Integer;
begin
  if CurUninstallStep <> usUninstall then
    exit;
  App := ExpandConstant('{app}');
  StopRunningFrom(App, Quoted(App + '\multiproxy_tray.py') + ' quit');
  if ExpandConstant('{param:PAGE|0}') = '1' then Items := PageAnswer else Items := SwitchedItems(False);
  if (Items <> '') and (ThisSettingsFolder <> App) then
  begin
    Json := RunHelper(App, 'remove --settings-dir ' + Quoted(ThisSettingsFolder) + ' --items ' + Items);
    ReportLeftovers(Json);
  end;
  Exec(ExpandConstant('{cmd}'), '/C ""' + App + '\python\python.exe" "' + App + '\installer_helper.py" forget --program-dir "' + App + '""', App, SW_HIDE, ewWaitUntilTerminated, Code);
  ForgetPrefillNote('multiproxy');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
    UseThePageFromTheAppsList('{0A43CE19-12C3-4285-A065-E2FBD58F8836}');
  if CurStep = ssPostInstall then
    WritePrefillNote('multiproxy', ExpandConstant('{app}'), SettingsFolder, Trim(ModelsEdit.Text), ChkWindows.Checked, ChkStartMenu.Checked);
end;
