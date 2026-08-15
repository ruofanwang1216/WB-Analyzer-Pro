; Inno Setup script for WB Analyzer Pro
; Build (on Windows, after `pyinstaller pyinstaller.spec` has produced dist\WBAnalyzerPro\):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; Output: dist_installer\WBAnalyzerPro-Setup-Windows.exe

#define MyAppName "WB Analyzer Pro"
#define MyAppVersion "0.3.0"
#define MyAppPublisher "WB-Lab"
#define MyAppExeName "WBAnalyzerPro.exe"

[Setup]
AppId={{B3B8B6D0-6C7E-4B1E-9C7C-7F1F6B9F0A11}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist_installer
OutputBaseFilename=WBAnalyzerPro-Setup-Windows
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\WBAnalyzerPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
