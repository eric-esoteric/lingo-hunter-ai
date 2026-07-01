; Inno Setup installer script for Lingo Hunter AI
; Uses relative paths so the project folder can be moved freely.

#define MyAppName "Lingo Hunter AI"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Lingo Hunter AI"
#define MyAppExeName "Lingo Hunter AI.exe"
#define MyProjectDir "."

[Setup]
AppId={{6F2C9C2B-6E9C-4C2A-9C1B-2C7C9A6E3C5D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes

LicenseFile={#MyProjectDir}\license.txt
SetupIconFile={#MyProjectDir}\icon.ico
OutputDir={#MyProjectDir}
OutputBaseFilename=LingoHunterAI_Setup
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern

ShowLanguageDialog=yes
LanguageDetectionMethod=none

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#MyProjectDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyProjectDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyProjectDir}\icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyProjectDir}\license.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
