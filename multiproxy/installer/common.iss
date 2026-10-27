; Pascal helpers shared by both installers (2026-10-02). Included from multiproxy.iss and courier.iss.
; Nothing here writes outside the folders the player chose on the pages, and nothing here touches Skyrim's folder or a mod manager.

[Code]
{ THE colours of our own text (2026-10-02). `WizardStyle=modern dynamic` makes Setup and Uninstall follow Windows' light or dark app setting and styles every Inno control,
  but not a colour we pick ourselves. Everything we colour comes from here, chosen by the active style (IsDarkInstallMode), never a literal in a page: a grey that is
  readable on the light pages (dark grey, about 6:1 on white) and one that is readable on the dark pages (light grey, about 7:1 on near-black). }
function HintColor: TColor;
begin
  if IsDarkInstallMode then
    Result := $00B0B0B0
  else
    Result := $00595959;
end;

{ The colour of a line that says something is wrong but nothing is lost (a folder that is gone, a pairing that would be lost): the dashboard's own warning amber, a dark amber on the
  light pages and a bright one on the dark pages (chosen like HintColor, never a literal in a page). }
function WarnColor: TColor;
begin
  if IsDarkInstallMode then
    Result := $001A94E8
  else
    Result := $00064A8A;
end;

{ A tick box's height from the font as drawn at this display's scaling (its default is shorter than the text at 150%, which cut off the lower part of p, g and y). }
function CheckHeight: Integer;
var
  M: TNewStaticText;
begin
  M := TNewStaticText.Create(WizardForm);
  M.Parent := WizardForm;
  M.AutoSize := True;
  M.Caption := 'Wg';
  Result := M.Height + ScaleY(6);
  M.Free;
end;

function CharAt(const S: String; I: Integer): String;
begin
  Result := Copy(S, I, 1);
end;

{ The helper program prints one line of JSON. These two readers are all of "JSON" the installer needs. }
function JsonString(const Text, Key: String): String;
var
  P, Q: Integer;
  Needle, Rest: String;
begin
  Result := '';
  Needle := '"' + Key + '": "';
  P := Pos(Needle, Text);
  if P = 0 then
    exit;
  Rest := Copy(Text, P + Length(Needle), Length(Text));
  Q := 1;
  while (Q <= Length(Rest)) and (CharAt(Rest, Q) <> '"') do
  begin
    if (CharAt(Rest, Q) = '\') and (Q < Length(Rest)) then
    begin
      Result := Result + CharAt(Rest, Q + 1);   { \\ becomes \ , \" becomes " }
      Q := Q + 2;
    end
    else
    begin
      Result := Result + CharAt(Rest, Q);
      Q := Q + 1;
    end;
  end;
end;

function JsonNumber(const Text, Key: String): Int64;
var
  P, Q: Integer;
  Needle, Rest, Digits: String;
begin
  Result := 0;
  Needle := '"' + Key + '": ';
  P := Pos(Needle, Text);
  if P = 0 then
    exit;
  Rest := Copy(Text, P + Length(Needle), 30);
  Digits := '';
  Q := 1;
  while (Q <= Length(Rest)) and (Pos(CharAt(Rest, Q), '0123456789') > 0) do
  begin
    Digits := Digits + CharAt(Rest, Q);
    Q := Q + 1;
  end;
  if Digits <> '' then
    Result := StrToInt64Def(Digits, 0);
end;

{ Runs the program's own Python on its helper and returns what it printed (empty if it failed). }
function RunHelper(const AppDir, Args: String): String;
var
  Py, Helper, OutFile, Line: String;
  Code: Integer;
  Text: AnsiString;
begin
  Result := '';
  Py := AppDir + '\python\python.exe';
  Helper := AppDir + '\installer_helper.py';
  OutFile := ExpandConstant('{tmp}') + '\helper-out.txt';
  DeleteFile(OutFile);
  Line := '/C ""' + Py + '" "' + Helper + '" ' + Args + ' > "' + OutFile + '" 2>&1"';
  if Exec(ExpandConstant('{cmd}'), Line, AppDir, SW_HIDE, ewWaitUntilTerminated, Code) then
    if LoadStringFromFile(OutFile, Text) then
      Result := Trim(String(Text));
end;

function Quoted(const S: String): String;
begin
  Result := '"' + S + '"';
end;

function FormatSize(Bytes: Int64): String;
begin
  if Bytes >= 1073741824 then
    Result := Format('%.1f GB', [Bytes / 1073741824.0])
  else if Bytes >= 1048576 then
    Result := Format('%.0f MB', [Bytes / 1048576.0])
  else
    Result := Format('%d KB', [Bytes div 1024]);
end;

{ Copies text to the clipboard through Windows' own clip.exe (no extra install, nothing to bundle). }
procedure CopyToClipboard(const Text: String);
var
  Code: Integer;
  Tmp: String;
begin
  Tmp := ExpandConstant('{tmp}') + '\clip.txt';
  SaveStringToFile(Tmp, Text, False);
  Exec(ExpandConstant('{cmd}'), '/C clip < "' + Tmp + '"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure OpenFolderSelecting(const FilePath: String);
var
  Code: Integer;
begin
  if FileExists(FilePath) then
    Exec(ExpandConstant('{win}') + '\explorer.exe', '/select,' + Quoted(FilePath), '', SW_SHOWNORMAL, ewNoWait, Code)
  else
    Exec(ExpandConstant('{win}') + '\explorer.exe', Quoted(ExtractFilePath(FilePath)), '', SW_SHOWNORMAL, ewNoWait, Code);
end;

{ Ends every program whose own executable sits inside Dir (matched by the folder the program runs from, never by its name). }
procedure StopProcessesUnder(const Dir: String);
var
  Code: Integer;
  Prefix, Ps: String;
begin
  Prefix := RemoveBackslashUnlessRoot(Dir) + '\';
  StringChangeEx(Prefix, '''', '''''', True);
  Ps := '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith(''' +
        Prefix + ''', [System.StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"';
  Exec(ExpandConstant('{sys}') + '\WindowsPowerShell\v1.0\powershell.exe', Ps, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

{ Closes a program of ours that is still running out of AppDir (ask nicely first, then end it). `QuitScript` is the tray/courier command that quits it. }
procedure StopRunningFrom(const AppDir, QuitArgs: String);
var
  Code: Integer;
begin
  if not DirExists(AppDir + '\python') then
    exit;
  if QuitArgs <> '' then
    Exec(AppDir + '\python\python.exe', QuitArgs, AppDir, SW_HIDE, ewWaitUntilTerminated, Code);
  StopProcessesUnder(AppDir + '\python');
end;

function TextFromFile(const Path: String): String;
var
  Text: AnsiString;
begin
  Result := '';
  if LoadStringFromFile(Path, Text) then
    Result := Trim(String(Text));
end;

{ ---- What the folder page pre-fills, and the small note left for the next run (2026-10-06) ----
  prefill.ps1 does the looking (the earlier settings file first, then the note the last install wrote in the registry, then the older install, then the defaults) and writes
  "key=value" lines; this only runs it and reads them. A scripted test names its own registry key with /REGKEY=, so it never touches the real one. }
var
  PrefillAnswers: TStringList;

function RegNoteKey(const App: String): String;
var
  Normal: String;
begin
  if App = 'courier' then
    Normal := 'Software\MultiProxy Courier'
  else
    Normal := 'Software\SkyrimNet MultiProxy';
  Result := ExpandConstant('{param:REGKEY|' + Normal + '}');
end;

function PrefillCommand(const App, Mode: String): String;
begin
  Result := '-NoProfile -ExecutionPolicy Bypass -File ' + Quoted(ExpandConstant('{tmp}') + '\prefill.ps1') + ' -App ' + App + ' -Mode ' + Mode +
            ' -RegKey ' + Quoted(RegNoteKey(App)) + ' -AppData ' + Quoted(ExpandConstant('{userappdata}')) + ' -LocalAppData ' + Quoted(ExpandConstant('{localappdata}'));
end;

procedure RunPrefill(const App, DefaultInstall, PreviousInstall, DefaultSettings: String);
var
  Code: Integer;
  Answers: String;
begin
  PrefillAnswers := TStringList.Create;
  try
    ExtractTemporaryFile('prefill.ps1');
    Answers := ExpandConstant('{tmp}') + '\prefill.txt';
    DeleteFile(Answers);
    Exec(ExpandConstant('{sys}') + '\WindowsPowerShell\v1.0\powershell.exe',
         PrefillCommand(App, 'discover') + ' -Out ' + Quoted(Answers) + ' -DefaultInstall ' + Quoted(DefaultInstall) + ' -PreviousInstall ' + Quoted(PreviousInstall) +
         ' -DefaultSettings ' + Quoted(DefaultSettings), '', SW_HIDE, ewWaitUntilTerminated, Code);
    if FileExists(Answers) then
      PrefillAnswers.LoadFromFile(Answers);
  except
    Log('Looking for the earlier answers did not work, so the defaults are used: ' + GetExceptionMessage);
  end;
end;

function PrefillValue(const Key: String): String;
var
  I, P: Integer;
begin
  Result := '';
  if PrefillAnswers = nil then
    exit;
  for I := 0 to PrefillAnswers.Count - 1 do
  begin
    P := Pos('=', PrefillAnswers[I]);
    if (P > 0) and (Copy(PrefillAnswers[I], 1, P - 1) = Key) then
    begin
      Result := Trim(Copy(PrefillAnswers[I], P + 1, Length(PrefillAnswers[I])));
      exit;
    end;
  end;
end;

{ The note for the next run: the folders and choices of this install. A value the settings file also holds wins over it, so it can never hide a newer one. }
procedure WritePrefillNote(const App, Install, Settings, Models: String; WithWindows, StartMenu: Boolean);
var
  Code: Integer;
  Win, Menu: String;
begin
  if WithWindows then Win := '1' else Win := '0';
  if StartMenu then Menu := '1' else Menu := '0';
  try
    ExtractTemporaryFile('prefill.ps1');
    Exec(ExpandConstant('{sys}') + '\WindowsPowerShell\v1.0\powershell.exe',
         PrefillCommand(App, 'write') + ' -Install ' + Quoted(Install) + ' -Settings ' + Quoted(Settings) + ' -Models ' + Quoted(Models) +
         ' -StartWithWindows ' + Win + ' -StartMenu ' + Menu, '', SW_HIDE, ewWaitUntilTerminated, Code);
  except
    Log('Saving the note for the next run did not work: ' + GetExceptionMessage);
  end;
end;

{ Uninstall takes its own note away and nothing else (the settings stay unless the person ticked them). }
procedure ForgetPrefillNote(const App: String);
begin
  RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, RegNoteKey(App));
end;

{ One plain line per folder that is no longer there. It is shown as typed, never replaced. }
function StaleFolderLines(const HasInstall, HasSettings, HasModels: Boolean): String;
begin
  Result := '';
  if HasInstall and (PrefillValue('staleInstall') = '1') then
    Result := Result + 'The install folder is missing. Pick a new one, or it will be created.' + #13#10;
  if HasSettings and (PrefillValue('staleSettings') = '1') then
    Result := Result + 'The settings folder is missing. Pick a new one, or it will be created.' + #13#10;
  if HasModels and (PrefillValue('staleModels') = '1') then
    Result := Result + 'The models folder is missing. Pick a new one, or it will be created.' + #13#10;
  Result := Trim(Result);
end;
