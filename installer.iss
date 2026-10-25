; 인터넷 설치 프로그램(Inno Setup 6.7): 설치할 때 앱 파일 zip을 받아 SHA-256을 확인하고 풀어 넣는다.
; 먼저 exe를 빌드하고 dist\file_organizer.zip을 만든 뒤 컴파일한다(README의 '설치 프로그램 만들기').
; 해시와 크기는 컴파일할 때 그 zip에서 읽으므로, Releases에는 같은 zip을 올려야 한다.
#define AppVersion "1.0.0"
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

[Files]
Source: "{#ZipUrl}"; DestName: "file_organizer.zip"; DestDir: "{app}"; Hash: "{#GetSHA256OfFile(ZipPath)}"; \
  ExternalSize: {#FileSize(ZipPath)}; Flags: external download extractarchive recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\파일 정리"; Filename: "{app}\file_organizer.exe"
Name: "{autodesktop}\파일 정리"; Filename: "{app}\file_organizer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\file_organizer.exe"; Description: "{cm:LaunchProgram,파일 정리}"; Flags: nowait postinstall skipifsilent

[Code]
function InstalledBefore: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\file_organizer.exe'));
end;
