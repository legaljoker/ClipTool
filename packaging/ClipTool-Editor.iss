; Inno Setup script for the ClipTool Editor installer.
; Built by .github/workflows/build-windows.yml:  iscc /DMyAppVersion=1.2.0 packaging\ClipTool-Editor.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{6E2B1C55-7A1F-4F51-9C1D-3C0B5D9A7E21}
AppName=ClipTool Editor
AppVersion={#MyAppVersion}
AppPublisher=ClipTool
DefaultDirName={autopf}\ClipTool Editor
DefaultGroupName=ClipTool Editor
; installs for the current user only - no administrator password needed
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=ClipTool-Editor-Setup-{#MyAppVersion}
SetupIconFile=cliptool.ico
UninstallDisplayIcon={app}\ClipTool-Editor.exe
UninstallDisplayName=ClipTool Editor
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\ClipTool-Editor\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\ClipTool Editor"; Filename: "{app}\ClipTool-Editor.exe"
Name: "{autodesktop}\ClipTool Editor"; Filename: "{app}\ClipTool-Editor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ClipTool-Editor.exe"; Description: "Start ClipTool Editor"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=ClipTool Editor is installed.%n%nYour projects and finished videos are kept in Documents\ClipTool.
