{ The uninstall page, shared by both installers (2026-10-02): ONE page, shown once before anything is removed, one tick box per kind of leftover.
  Only the kinds that exist on this device are listed, each with where it is and how big. Settings, keys, models, voice files and kept downloads start UNTICKED
  (they are kept unless the player ticks them); the program (locked on) and, for the Courier, its Windows Firewall rule start ticked.
  Skyrim, a mod manager and models the player added by path are never listed and never touched. }

type
  TWorkRect = record
    Left, Top, Right, Bottom: Integer;
  end;

function SystemParametersInfo(Action, Param: Integer; var Rect: TWorkRect; WinIni: Integer): Integer; external 'SystemParametersInfoW@user32.dll stdcall';
function GetSystemMetrics(Index: Integer): Integer; external 'GetSystemMetrics@user32.dll stdcall';

const
  MeasureFile = 'leftovers-sizes.json';

var
  UnForm: TSetupForm;
  UnMeter: TNewStaticText;
  UnLineH, UnRowWidth, UnDetailWidth: Integer;
  UnList: TNewCheckListBox;      { only when the rows would not fit the screen: the same rows as a scrolling list }
  UnItemAt, UnDetailAt: array of Integer;
  UnChecks: array of TNewCheckBox;
  UnDetails: array of TNewStaticText;
  UnIds: array of String;
  UnPlaces: array of String;
  UnSizeFile: String;
  UnSizesShown: Boolean;

function ItemLabel(const Id: String; Courier: Boolean): String;
begin
  if Id = 'settings' then
  begin
    if Courier then
      Result := 'This device''s settings and pairing code'
    else
      Result := 'Your settings and saved keys';
  end
  else if Id = 'models' then
  begin
    if Courier then
      Result := 'Local AI models this Courier downloaded'
    else
      Result := 'Local AI models MultiProxy downloaded';
  end
  else if Id = 'voice' then
    Result := 'Higgs voice models'
  else if Id = 'clips' then
  begin
    if Courier then
      Result := 'Voice clips kept on this device'
    else
      Result := 'Your own voice clips';
  end
  else if Id = 'downloads' then
    Result := 'Files downloaded for your other devices'
  else
    Result := Id;
end;

{ The text of one item in the helper's JSON, from its id on. }
function ItemBlock(const Json, Id: String): String;
var
  P: Integer;
begin
  Result := '';
  P := Pos('"id": "' + Id + '"', Json);
  if P > 0 then
    Result := Copy(Json, P, 2000);
end;

{ First path of an item, as the player should read it (the JSON doubles backslashes). }
function FirstPath(const Block: String): String;
var
  P: Integer;
  Rest: String;
begin
  Result := '';
  P := Pos('"paths": ["', Block);
  if P = 0 then
    exit;
  Rest := Copy(Block, P + 11, Length(Block));
  Result := Copy(Rest, 1, Pos('"', Rest) - 1);
  StringChangeEx(Result, '\\', '\', True);
end;

{ How wide a text is at this display's scaling: a text of the form's own font that is never shown (UnMeter) is asked. }
function TextW(const S: String): Integer;
begin
  UnMeter.Caption := S;
  Result := UnMeter.Width;
end;

{ A long path gives up its middle (start ... end) rather than widening the window. }
function ShortPath(const S: String; MaxW: Integer): String;
var
  Keep, L: Integer;
begin
  Result := S;
  if (MaxW <= 0) or (TextW(Result) <= MaxW) then
    exit;
  Keep := Length(S);
  while Keep > 8 do
  begin
    Keep := Keep - 2;
    L := (Keep + 1) div 2;
    Result := Copy(S, 1, L) + '…' + Copy(S, Length(S) - (Keep - L) + 1, Keep - L);
    if TextW(Result) <= MaxW then
      exit;
  end;
end;

procedure SetDetail(N: Integer; const Suffix: String);
begin
  if UnPlaces[N] = '' then
    exit;
  if UnList <> nil then
  begin
    UnList.ItemCaption[UnDetailAt[N]] := ShortPath(UnPlaces[N], UnDetailWidth - ScaleX(30) - TextW(Suffix)) + Suffix;
    exit;
  end;
  { a long folder path gives up its middle (never widens the window); the whole path is the hover text }
  UnDetails[N].Caption := ShortPath(UnPlaces[N], UnDetailWidth - TextW(Suffix)) + Suffix;
  UnDetails[N].Hint := UnPlaces[N];
  UnDetails[N].ShowHint := True;
end;

procedure ShowSizes;
var
  Json: String;
  I: Integer;
begin
  if UnSizesShown or (UnSizeFile = '') then
    exit;
  Json := TextFromFile(UnSizeFile);
  if (Json = '') or (Pos(']}', Json) = 0) then
    exit;
  UnSizesShown := True;
  for I := 0 to GetArrayLength(UnIds) - 1 do
    if UnIds[I] <> 'firewall' then
      SetDetail(I, '   ·   ' + FormatSize(JsonNumber(ItemBlock(Json, UnIds[I]), 'bytes')));
end;

function FirewallRuleExists(const Name: String): Boolean;
var
  Code: Integer;
  Show: String;
begin
  Show := ExpandConstant('{tmp}') + '\fw-uninstall.txt';
  DeleteFile(Show);
  Exec(ExpandConstant('{cmd}'), '/C netsh advfirewall firewall show rule name="' + Name + '" > "' + Show + '" 2>&1', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := Pos(Name, TextFromFile(Show)) > 0;
end;

function UnTicked(I: Integer): Boolean;
begin
  if UnList <> nil then
    Result := UnList.Checked[UnItemAt[I]]
  else
    Result := UnChecks[I].Checked;
end;

{ One row: a tick box, and under it (grey) where it is and how big. Every height comes from the font as it is drawn at this display's scaling
  (UnLineH), never from a fixed number, so nothing is cut off at any scaling. `Top` moves down to where the next row starts. }
procedure AddUnRow(Parent: TWinControl; const Id, Caption, Place: String; Ticked, Locked: Boolean; var Top: Integer);
var
  N: Integer;
begin
  N := GetArrayLength(UnIds);
  SetArrayLength(UnIds, N + 1);
  SetArrayLength(UnPlaces, N + 1);
  SetArrayLength(UnChecks, N + 1);
  SetArrayLength(UnDetails, N + 1);
  SetArrayLength(UnItemAt, N + 1);
  SetArrayLength(UnDetailAt, N + 1);
  UnIds[N] := Id;
  UnPlaces[N] := Place;
  if UnList <> nil then
  begin
    UnItemAt[N] := UnList.AddCheckBox(Caption, '', 0, Ticked, not Locked, False, True, nil);
    if Place <> '' then
    begin
      UnDetailAt[N] := UnList.AddGroup(' ', '', 0, nil);
      UnList.ItemEnabled[UnDetailAt[N]] := False;
    end;
    UnList.AddGroup('', '', 0, nil);
    exit;
  end;
  UnChecks[N] := TNewCheckBox.Create(Parent);
  UnChecks[N].Parent := Parent;
  UnChecks[N].Left := 0;
  UnChecks[N].Top := Top;
  UnChecks[N].Width := UnRowWidth;
  UnChecks[N].Height := UnLineH + ScaleY(6);
  UnChecks[N].Caption := Caption;
  UnChecks[N].Checked := Ticked;
  UnChecks[N].Enabled := not Locked;
  Top := Top + UnChecks[N].Height;
  UnDetails[N] := TNewStaticText.Create(Parent);
  UnDetails[N].Parent := Parent;
  UnDetails[N].Left := ScaleX(20);
  UnDetails[N].Top := Top + ScaleY(1);
  UnDetails[N].Width := UnDetailWidth;
  UnDetails[N].AutoSize := False;
  UnDetails[N].Height := UnLineH + ScaleY(2);
  UnDetails[N].Font.Color := HintColor;
  UnDetails[N].Visible := Place <> '';
  if Place <> '' then
  begin
    UnDetails[N].Caption := Place;
    Top := Top + UnDetails[N].Height + ScaleY(1);
  end;
  { a clear gap between rows, bigger than the one inside a row, so each tick box visibly belongs to the path under it }
  Top := Top + ScaleY(16);
end;

{ Shows the page. False if the player cancelled; otherwise `Selected` is the comma list of ticked kinds ('firewall' included for the Courier).
  The window is sized from what is on it: a normal wizard-page width, and a height of heading + sentence + the rows that exist + the quiet line + buttons. It is
  scaled ONCE, here (CreateCustomForm scales the size it is given, so it is given a token size and the real one is set below, in pixels). If the rows would
  not fit the screen they scroll while the heading and buttons stay put. }
function AskWhatToRemove(const Title: String; Courier: Boolean; const ProgramDir, SettingsDir: String; var Selected: String): Boolean;
var
  Info, Id, Rest, Cmd: String;
  Top, I, Code, P, ListH, MaxListH, ChromeH, HeadH, FootH, BtnH, Pad, Rows: Integer;
  Heading, Sub, Foot: TNewStaticText;
  Ok, Cancel: TNewButton;
  Surface: TPanel;
  Scroll: TWinControl;
  Holder: TPanel;
  Work: TWorkRect;
begin
  Result := True;
  Selected := '';
  Info := '';
  SetArrayLength(UnIds, 0);
  if DirExists(SettingsDir) and (SettingsDir <> ProgramDir) then
  begin
    Cmd := 'leftovers --settings-dir ' + Quoted(SettingsDir);
    if Courier then
      Cmd := Cmd + ' --courier';
    Info := RunHelper(ProgramDir, Cmd);
  end;
  UnForm := CreateCustomForm(ScaleX(100), ScaleY(100), True, False);
  UnForm.Caption := Title;
  Pad := ScaleX(20);
  UnForm.ClientWidth := ScaleX(580);
  UnRowWidth := UnForm.ClientWidth - 2 * Pad - ScaleX(24);          { room for a scroll bar }
  UnDetailWidth := UnRowWidth - ScaleX(20);
  { a text of the form's own font, never shown, only to ask how wide a text is and how tall a line is at this scaling }
  UnMeter := TNewStaticText.Create(UnForm);
  UnMeter.Parent := UnForm;
  UnMeter.AutoSize := True;
  UnMeter.Left := -ScaleX(4000);
  UnMeter.Caption := 'Wg';
  UnLineH := UnMeter.Height;
  Surface := TPanel.Create(UnForm);
  Surface.Parent := UnForm;
  Surface.Left := 0;
  Surface.Top := 0;
  Surface.Width := UnForm.ClientWidth;
  Surface.BevelOuter := bvNone;
  Surface.ParentColor := True;                { the form's own colour, light or dark: never the panel's default light grey }
  Surface.ParentBackground := True;
  Heading := TNewStaticText.Create(UnForm);
  Heading.Parent := Surface;
  Heading.Left := Pad;
  Heading.Top := ScaleY(16);
  Heading.AutoSize := True;
  Heading.Caption := Title;
  Heading.Font.Style := [fsBold];
  Heading.Font.Size := Heading.Font.Size + 3;
  Sub := TNewStaticText.Create(UnForm);
  Sub.Parent := Surface;
  Sub.Left := Pad;
  Sub.Top := Heading.Top + Heading.Height + ScaleY(8);
  Sub.Width := UnForm.ClientWidth - 2 * Pad;
  Sub.AutoSize := True;
  Sub.WordWrap := True;
  Sub.Caption := 'Ticked items will be removed. Your settings and downloads are kept unless you tick them too.';
  HeadH := Sub.Top + Sub.Height + ScaleY(14);
  { how many rows there will be, to know before drawing them whether they fit the screen }
  Rows := 1;
  Rest := Info;
  while Pos('"id": "', Rest) > 0 do
  begin
    Rest := Copy(Rest, Pos('"id": "', Rest) + 7, Length(Rest));
    Rows := Rows + 1;
  end;
  if Courier and FirewallRuleExists('MultiProxy Courier') then
    Rows := Rows + 1;
  Foot := TNewStaticText.Create(UnForm);
  Foot.Parent := Surface;
  Foot.Left := Pad;
  Foot.Width := UnForm.ClientWidth - 2 * Pad;
  Foot.AutoSize := True;
  Foot.WordWrap := True;
  Foot.Font.Color := HintColor;
  Foot.Caption := 'Skyrim, your mod manager and models you added yourself are never touched.';
  FootH := Foot.Height;
  BtnH := UnLineH + ScaleY(14);
  ChromeH := UnForm.Height - UnForm.ClientHeight;
  if SystemParametersInfo(48, 0, Work, 0) = 0 then       { SPI_GETWORKAREA }
  begin
    Work.Left := 0;
    Work.Top := 0;
    Work.Right := GetSystemMetrics(0);
    Work.Bottom := GetSystemMetrics(1);
  end;
  MaxListH := (Work.Bottom - Work.Top) - ChromeH - HeadH - FootH - BtnH - ScaleY(70);
  UnList := nil;
  Scroll := nil;
  if Rows * (UnLineH * 2 + ScaleY(6 + 2 + 1 + 1 + 16)) > MaxListH then
  begin
    UnList := TNewCheckListBox.Create(UnForm);
    UnList.Parent := Surface;
    UnList.Left := Pad;
    UnList.Top := HeadH;
    UnList.Width := UnForm.ClientWidth - 2 * Pad;
    UnList.Flat := True;
    UnList.BorderStyle := bsNone;
    UnList.ParentColor := True;
    Scroll := UnList;
  end
  else
  begin
    Holder := TPanel.Create(UnForm);
    Holder.Parent := Surface;
    Holder.Left := Pad;
    Holder.Top := HeadH;
    Holder.Width := UnForm.ClientWidth - 2 * Pad;
    Holder.BevelOuter := bvNone;
    Holder.ParentColor := True;
    Holder.ParentBackground := True;
    Scroll := Holder;
  end;
  Top := 0;
  AddUnRow(Scroll, 'program', 'The program', ProgramDir, True, True, Top);
  Rest := Info;
  repeat
    P := Pos('"id": "', Rest);
    if P = 0 then
      break;
    Rest := Copy(Rest, P + 7, Length(Rest));
    Id := Copy(Rest, 1, Pos('"', Rest) - 1);
    AddUnRow(Scroll, Id, ItemLabel(Id, Courier), FirstPath(ItemBlock(Info, Id)), False, False, Top);
  until False;
  if Courier and FirewallRuleExists('MultiProxy Courier') then
    AddUnRow(Scroll, 'firewall', 'The Windows Firewall rule (Windows will ask for permission)', '', True, False, Top);
  for I := 0 to GetArrayLength(UnIds) - 1 do
    if UnIds[I] <> 'firewall' then
      SetDetail(I, '   ·   working out the size…');
  ListH := Top;
  if UnList <> nil then
    ListH := MaxListH
  else if ListH > MaxListH then
    ListH := MaxListH;
  Scroll.Height := ListH;
  Foot.Top := Scroll.Top + ListH + ScaleY(4);
  Surface.Height := Foot.Top + FootH + ScaleY(10);
  UnForm.ClientHeight := Surface.Height + BtnH + ScaleY(26);
  UnForm.Left := Work.Left + ((Work.Right - Work.Left) - UnForm.Width) div 2;
  UnForm.Top := Work.Top + ((Work.Bottom - Work.Top) - UnForm.Height) div 2;
  Ok := TNewButton.Create(UnForm);
  Ok.Parent := UnForm;
  Ok.Caption := 'Uninstall';
  Ok.ModalResult := mrOk;
  Ok.Default := True;
  Ok.Width := ScaleX(100);
  Ok.Height := BtnH;
  Ok.Top := Surface.Height + ScaleY(12);
  Cancel := TNewButton.Create(UnForm);
  Cancel.Parent := UnForm;
  Cancel.Caption := 'Cancel';
  Cancel.ModalResult := mrCancel;
  Cancel.Cancel := True;
  Cancel.Width := ScaleX(100);
  Cancel.Height := BtnH;
  Cancel.Left := UnForm.ClientWidth - Pad - Cancel.Width;
  Ok.Left := Cancel.Left - ScaleX(8) - Ok.Width;
  Cancel.Top := Ok.Top;
  { the sizes are worked out in the background, so the page is up at once (the program's own size too: the helper is asked for every row) }
  UnSizeFile := ExpandConstant('{tmp}') + '\' + MeasureFile;
  DeleteFile(UnSizeFile);
  UnSizesShown := False;
  Cmd := '/C ""' + ProgramDir + '\python\python.exe" "' + ProgramDir + '\installer_helper.py" leftovers --settings-dir ' + Quoted(SettingsDir) + ' --program-dir ' + Quoted(ProgramDir) + ' --sizes';
  if Courier then
    Cmd := Cmd + ' --courier';
  Cmd := Cmd + ' > "' + UnSizeFile + '" 2>&1"';
  Exec(ExpandConstant('{cmd}'), Cmd, ProgramDir, SW_HIDE, ewNoWait, Code);
  { Inno's scripting has no timer and no message pump, so a page can't be updated while it is open. The helper starts working out the sizes before the page
    opens and the page waits for it for a moment (a folder of a few big files is measured at once); a row it hasn't finished says so instead of waiting. }
  for I := 1 to 40 do
  begin
    ShowSizes;
    if UnSizesShown then
      break;
    Sleep(50);
  end;
  if not UnSizesShown then
    for I := 0 to GetArrayLength(UnIds) - 1 do
      if UnIds[I] <> 'firewall' then
        SetDetail(I, '   ·   size not worked out yet');
  if UnForm.ShowModal = mrOk then
  begin
    for I := 0 to GetArrayLength(UnIds) - 1 do
      if UnTicked(I) and (UnIds[I] <> 'program') then
      begin
        if Selected <> '' then
          Selected := Selected + ',';
        Selected := Selected + UnIds[I];
      end;
  end
  else
    Result := False;
  UnForm.Free;
end;

procedure AppendId(var List: String; const Id: String);
begin
  if List <> '' then
    List := List + ',';
  List := List + Id;
end;

{ What a silent uninstall removes: only what the switches name (nothing by default), so a scripted uninstall keeps settings, models and voice files. }
function SwitchedItems(Courier: Boolean): String;
begin
  Result := '';
  if ExpandConstant('{param:REMOVESETTINGS|0}') = '1' then AppendId(Result, 'settings');
  if ExpandConstant('{param:REMOVEMODELS|0}') = '1' then AppendId(Result, 'models');
  if ExpandConstant('{param:REMOVEVOICE|0}') = '1' then AppendId(Result, 'voice');
  if ExpandConstant('{param:REMOVECLIPS|0}') = '1' then AppendId(Result, 'clips');
  if (not Courier) and (ExpandConstant('{param:REMOVEDOWNLOADS|0}') = '1') then AppendId(Result, 'downloads');
  if Courier and (ExpandConstant('{param:REMOVEFIREWALL|0}') = '1') then AppendId(Result, 'firewall');
end;

{ One plain list at the end of what could not be removed (a file in use), not an error per file. }
procedure ReportLeftovers(const Json: String);
var
  Rest, Path, Msg: String;
  P, N: Integer;
begin
  Rest := Json;
  Msg := '';
  N := 0;
  repeat
    P := Pos('"path": "', Rest);
    if P = 0 then
      break;
    Rest := Copy(Rest, P + 9, Length(Rest));
    Path := Copy(Rest, 1, Pos('"', Rest) - 1);
    StringChangeEx(Path, '\\', '\', True);
    N := N + 1;
    if N <= 12 then
      Msg := Msg + #13#10 + Path;
  until False;
  if N > 0 then
  begin
    if N > 12 then
      Msg := Msg + #13#10 + '... and ' + IntToStr(N - 12) + ' more';
    SuppressibleMsgBox('These could not be removed because something is still using them:' + Msg + #13#10 + #13#10 +
                       'Close whatever is using them and delete them yourself.', mbInformation, MB_OK, IDOK);
  end;
end;

{ After the uninstall asked for the Windows Firewall rule to go: look again (read-only, no administrator). If it is still there, Windows never got the person's OK (a permission pop-up that was
  missed, hidden behind another window or declined), so say so once, in plain words, with the one command that finishes the job. A rule that is gone says nothing. A silent uninstall shows no box;
  the log has the line. The program's own helper does the looking (installer_helper.py firewall-check); if it gives no answer, the same look the page took (FirewallRuleExists) stands in. }
procedure ReportFirewallLeftover(const AppDir, Name: String);
var
  Json: String;
  There: Boolean;
begin
  Json := RunHelper(AppDir, 'firewall-check --name ' + Quoted(Name));
  if Pos('"present": true', Json) > 0 then
    There := True
  else if Pos('"present": false', Json) > 0 then
    There := False
  else
    There := FirewallRuleExists(Name);
  if not There then
    exit;
  Log('The Windows Firewall rule "' + Name + '" is still there after the uninstall asked for it to go.');
  SuppressibleMsgBox('The Windows Firewall rule is still there, because Windows didn''t get your OK to remove it. It is only for your home network and does no harm. ' +
                     'To remove it later, run this in an administrator command prompt:' + #13#10 + #13#10 +
                     'netsh advfirewall firewall delete rule name="' + Name + '"', mbInformation, MB_OK, IDOK);
end;

{ The Apps list's Uninstall button runs the uninstaller quietly (no Are-you-sure box and no Done box: the page is the one question) with /PAGE=1, which tells it
  to show the page. A scripted uninstall (/VERYSILENT and the /REMOVE... switches) never sees it. `AppGuid` is the installer's AppId, braces included. }
procedure UseThePageFromTheAppsList(const AppGuid: String);
begin
  RegWriteStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + AppGuid + '_is1', 'UninstallString',
                      '"' + ExpandConstant('{uninstallexe}') + '" /SILENT /PAGE=1');
end;
