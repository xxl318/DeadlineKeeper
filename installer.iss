; ============================================================
;  DeadlineKeeper - Inno Setup 安装程序脚本
;  运行“一键生成Windows软件.bat”时会自动调用本文件
; ============================================================

#define MyAppName       "DeadlineKeeper"
#define MyAppVersion    "1.0.0"
#define MyAppPublisher  "DeadlineKeeper"
#define MyAppExeName    "DeadlineKeeper.exe"

[Setup]
AppId={{B7A4D2E3-5F8C-4A1B-9E2D-3C6F8A0B5D7E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=DeadlineKeeper-Setup
OutputDir=Output
SetupIconFile=deadlinekeeper.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "en";  MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\DeadlineKeeper.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}";    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
