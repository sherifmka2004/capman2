; Inno Setup script for capman2 Windows installer
; Build: iscc packaging\windows\capman2.iss (from repo root)

[Setup]
AppName=capman2
AppVersion=0.1.0
AppPublisher=capman2
AppPublisherURL=https://github.com/sherifmka2004/capman2
DefaultDirName={autopf}\capman2
DefaultGroupName=capman2
OutputDir=..\..\dist
OutputBaseFilename=capman2-windows-x86_64-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\capman2.exe
DisableProgramGroupPage=yes

[Files]
Source: "..\..\dist\capman2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\capman2"; Filename: "{app}\capman2.exe"; WorkingDir: "{app}"
Name: "{commondesktop}\capman2"; Filename: "{app}\capman2.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\capman2.exe"; Description: "Launch capman2"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
