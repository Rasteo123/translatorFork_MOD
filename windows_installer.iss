[Setup]
; Уникальный идентификатор приложения
AppId={{5E1C3C0B-8D52-4C67-B9B2-3A6D3A0A7A1B}
AppName=Gemini Translator
AppVersion=10.5.21
AppPublisher=SiberianTeam
AppPublisherURL=https://github.com/Rasteo123/translatorFork_MOD
AppSupportURL=https://github.com/Rasteo123/translatorFork_MOD/issues
AppUpdatesURL=https://github.com/Rasteo123/translatorFork_MOD/releases

; Установка только для текущего пользователя (без прав админа)
PrivilegesRequired=lowest
DefaultDirName={autopf}\GeminiTranslator
DefaultGroupName=Gemini Translator
DisableProgramGroupPage=yes

; Настройки внешнего вида и выходного файла
OutputDir=installer_output
OutputBaseFilename=GeminiTranslator-Setup
SetupIconFile=gemini_translator\GT.ico
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Основной исполняемый файл
Source: "dist\translatorFork_MOD.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Ярлык в меню "Пуск". ВАЖНО: AppUserModelID позволяет уведомлениям показывать имя программы
Name: "{autoprograms}\Gemini Translator"; Filename: "{app}\translatorFork_MOD.exe"; AppUserModelID: "SiberianTeam.GeminiTranslator"
; Ярлык на рабочем столе
Name: "{autodesktop}\Gemini Translator"; Filename: "{app}\translatorFork_MOD.exe"; Tasks: desktopicon; AppUserModelID: "SiberianTeam.GeminiTranslator"

[Run]
Filename: "{app}\translatorFork_MOD.exe"; Description: "{cm:LaunchProgram,Gemini Translator}"; Flags: nowait postinstall skipifsilent
