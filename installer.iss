; 인터넷 설치 프로그램(Inno Setup 6.7): 설치할 때 앱 파일 zip을 받아 SHA-256을 확인하고 풀어 넣는다.
; 먼저 exe를 빌드하고 dist\file_organizer.zip을 만든 뒤 컴파일한다(README의 '설치 프로그램 만들기').
; 해시와 크기는 컴파일할 때 그 zip에서 읽으므로, Releases에는 같은 zip을 올려야 한다.
; 버전은 릴리스 워크플로가 태그에서 넘긴다(ISCC /DAppVersion=1.0.2). 태그 없이 만든 시험 빌드는 0.0.0이고, 앱은 그 버전에서 새 버전을 묻지 않는다.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef ZipUrl
  #define ZipUrl "https://github.com/jinhangcho-ops/file_organizer/releases/download/v" + AppVersion + "/file_organizer.zip"
#endif
#define ZipPath AddBackslash(SourcePath) + "dist\file_organizer.zip"
#if !FileExists(ZipPath)
  #error dist\file_organizer.zip이 없습니다
#endif

[Setup]
AppId={{304EE660-B6BF-470B-BC13-BF179A6A61A4}
AppName=파일 정리
AppVersion={#AppVersion}
AppPublisher=jinhangcho-ops
; 켜져 있거나 창을 닫고 작업을 마치는 중인 프로그램(desktop.py의 MUTEX)이 있으면 설치·제거 전에 닫으라고 한다.
AppMutex=file_organizer_app
DefaultDirName={autopf}\file_organizer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ArchiveExtraction=full
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\file_organizer.exe
OutputDir=dist
OutputBaseFilename=file_organizer_setup

[Languages]
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; 고친 판으로 덮어쓸 때 옛 부품이 섞이지 않게. 이 앱이 설치된 폴더일 때만 지운다.
Type: filesandordirs; Name: "{app}\_internal"; Check: InstalledBefore

[INI]
; 앱이 새 버전 알림에 쓰는 설치된 버전(update.installed)
Filename: "{app}\version.ini"; Section: "app"; Key: "version"; String: "{#AppVersion}"

[UninstallDelete]
; 설치가 적은 버전 파일(지우지 않으면 프로그램 폴더가 남는다)
Type: files; Name: "{app}\version.ini"

[Files]
Source: "{#ZipUrl}"; DestName: "file_organizer.zip"; DestDir: "{app}"; Hash: "{#GetSHA256OfFile(ZipPath)}"; \
  ExternalSize: {#FileSize(ZipPath)}; Flags: external download extractarchive recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\파일 정리"; Filename: "{app}\file_organizer.exe"
Name: "{autodesktop}\파일 정리"; Filename: "{app}\file_organizer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\file_organizer.exe"; Description: "{cm:LaunchProgram,파일 정리}"; Flags: nowait postinstall skipifsilent
; 앱의 [지금 업데이트](조용한 설치)는 /RELAUNCH를 붙인다: 그때만 설치 뒤 다시 연다(skipifsilent를 빼야 조용한 설치에서도 돈다).
Filename: "{app}\file_organizer.exe"; Flags: nowait postinstall; Check: Relaunch

[Code]
function Relaunch: Boolean;
begin
  Result := WizardSilent and (Pos('/RELAUNCH', UpperCase(GetCmdTail)) > 0);
end;

// [지금 업데이트]: 앱은 설치를 띄운 뒤 창을 닫고 끝난다. 그때까지(뮤텍스가 없어질 때까지, 최대 30초) 기다린다.
// InitializeSetup은 AppMutex 확인보다 먼저 돌아서, 앱이 끝난 뒤에 확인하고 파일을 바꾼다.
function InitializeSetup: Boolean;
var
  I: Integer;
begin
  if Relaunch then
    for I := 1 to 60 do
    begin
      if not CheckForMutexes('file_organizer_app') then
        Break;
      Sleep(500);
    end;
  Result := True;
end;

function InstalledBefore: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\file_organizer.exe'));
end;
