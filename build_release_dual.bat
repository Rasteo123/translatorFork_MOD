@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if not "%~1"=="" set "NON_INTERACTIVE=1"

call :resolve_python
if %ERRORLEVEL% NEQ 0 goto :eof

call :resolve_release_version
if %ERRORLEVEL% NEQ 0 goto :eof

if /I "%~1"=="translator" goto build_translator
if /I "%~1"=="full" goto build_full
if /I "%~1"=="all" goto build_all

:menu
cls
echo ======================================================
echo   Dual release build
echo ======================================================
echo.
echo   1. Small exe: translator only
echo   2. Full exe: all features
echo   3. Build both
echo   4. Exit
echo.
echo ======================================================
set /p choice="Select action (1, 2, 3, or 4): "

if not defined choice goto menu
if "%choice%"=="1" goto build_translator
if "%choice%"=="2" goto build_full
if "%choice%"=="3" goto build_all
if "%choice%"=="4" goto :eof

echo Invalid choice.
if not defined NON_INTERACTIVE pause
goto menu

:resolve_python
if exist "%cd%\.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%cd%\.venv\Scripts\python.exe"
) else if exist "%cd%\venv\Scripts\python.exe" (
    set "PYTHON_CMD=%cd%\venv\Scripts\python.exe"
) else (
    set "PYTHON_CMD=python"
)

if /I "%PYTHON_CMD%"=="python" (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Python was not found.
        if not defined NON_INTERACTIVE pause
        exit /b 1
    )
) else if not exist "%PYTHON_CMD%" (
    echo [ERROR] Python interpreter was not found: %PYTHON_CMD%
    if not defined NON_INTERACTIVE pause
    exit /b 1
)

echo [INFO] Python: %PYTHON_CMD%
exit /b 0

:resolve_release_version
for /f "delims=" %%V in ('"%PYTHON_CMD%" -c "from gemini_translator.version import __version__; print(__version__)"') do set "RELEASE_VERSION=%%V"
if not defined RELEASE_VERSION (
    echo [ERROR] Failed to resolve release version.
    if not defined NON_INTERACTIVE pause
    exit /b 1
)
set "RELEASE_DIR=dist\release-v%RELEASE_VERSION%-upload"
echo [INFO] Release version: %RELEASE_VERSION%
exit /b 0

:prepare_build
echo.
echo [STEP] Installing build dependencies...
"%PYTHON_CMD%" -m pip install --upgrade -r "requirements.txt" pyinstaller pyinstaller-hooks-contrib
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install build dependencies.
    if not defined NON_INTERACTIVE pause
    exit /b 1
)

if exist "dist\chatgpt-profile-run" rmdir /S /Q "dist\chatgpt-profile-run"
if exist "dist\logs" rmdir /S /Q "dist\logs"
if exist "dist\translatorFork-translator.exe" del /Q "dist\translatorFork-translator.exe"
if exist "dist\translatorFork-full.exe" del /Q "dist\translatorFork-full.exe"
if exist "dist\translatorFork-translator" rmdir /S /Q "dist\translatorFork-translator"
if exist "dist\translatorFork-full" rmdir /S /Q "dist\translatorFork-full"
if exist "%RELEASE_DIR%" rmdir /S /Q "%RELEASE_DIR%"
if exist "build\translatorFork-translator" rmdir /S /Q "build\translatorFork-translator"
if exist "build\translatorFork-full" rmdir /S /Q "build\translatorFork-full"
exit /b 0

:build_translator
call :prepare_build
if %ERRORLEVEL% NEQ 0 goto :eof
echo.
echo [STEP] Building small translator-only release...
"%PYTHON_CMD%" -m PyInstaller --clean --noconfirm "translatorFork-translator-only.spec"
call :finish_build "dist\translatorFork-translator"
goto :eof

:build_full
call :prepare_build
if %ERRORLEVEL% NEQ 0 goto :eof
echo.
echo [STEP] Building full release...
"%PYTHON_CMD%" -m PyInstaller --clean --noconfirm "translatorFork-full.spec"
call :finish_build "dist\translatorFork-full"
goto :eof

:build_all
call :prepare_build
if %ERRORLEVEL% NEQ 0 goto :eof
echo.
echo [STEP] Building small translator-only release...
"%PYTHON_CMD%" -m PyInstaller --clean --noconfirm "translatorFork-translator-only.spec"
if %ERRORLEVEL% NEQ 0 (
    call :finish_build ""
    goto :eof
)
echo.
echo [STEP] Building full release...
"%PYTHON_CMD%" -m PyInstaller --clean --noconfirm "translatorFork-full.spec"
call :finish_build "dist\translatorFork-translator" "dist\translatorFork-full"
goto :eof

:finish_build
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Build failed.
    if not defined NON_INTERACTIVE pause
    exit /b 1
)

if not "%~1"=="" if not exist "%~1\" (
    echo.
    echo [ERROR] Expected output folder was not found: %~1
    if not defined NON_INTERACTIVE pause
    exit /b 1
)
if not "%~2"=="" if not exist "%~2\" (
    echo.
    echo [ERROR] Expected output folder was not found: %~2
    if not defined NON_INTERACTIVE pause
    exit /b 1
)

call :package_release "%~1" "%~2"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Release packaging failed.
    if not defined NON_INTERACTIVE pause
    exit /b 1
)

echo.
echo [OK] Build finished.
if not "%~1"=="" echo     %~1
if not "%~2"=="" echo     %~2
echo.
echo [OK] Release assets:
echo     %RELEASE_DIR%
if not defined NON_INTERACTIVE pause
exit /b 0

:package_release
echo.
echo [STEP] Packaging release assets...
if not exist "dist" mkdir "dist"
if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"

if not "%~1"=="" call :package_output "%~1"
if %ERRORLEVEL% NEQ 0 exit /b 1
if not "%~2"=="" call :package_output "%~2"
if %ERRORLEVEL% NEQ 0 exit /b 1

call :create_source_archive
if %ERRORLEVEL% NEQ 0 exit /b 1

call :write_sha256sums
if %ERRORLEVEL% NEQ 0 exit /b 1

exit /b 0

:package_output
set "OUTPUT_DIR=%~1"
set "OUTPUT_NAME=%~nx1"
if /I "%OUTPUT_NAME%"=="translatorFork-translator" (
    set "ZIP_NAME=translatorFork-translator-v%RELEASE_VERSION%-windows.zip"
) else if /I "%OUTPUT_NAME%"=="translatorFork-full" (
    set "ZIP_NAME=translatorFork-full-v%RELEASE_VERSION%-windows.zip"
) else (
    echo [ERROR] Unknown output folder: %OUTPUT_DIR%
    exit /b 1
)

echo [INFO] Creating %ZIP_NAME%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $zip = Join-Path (Resolve-Path -LiteralPath '%RELEASE_DIR%') '%ZIP_NAME%'; if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }; Compress-Archive -LiteralPath '%OUTPUT_DIR%' -DestinationPath $zip -Force"
if %ERRORLEVEL% NEQ 0 exit /b 1
exit /b 0

:create_source_archive
set "SOURCE_ZIP=%RELEASE_DIR%\translatorFork_MOD-source-v%RELEASE_VERSION%.zip"
echo [INFO] Creating translatorFork_MOD-source-v%RELEASE_VERSION%.zip...
git archive --format=zip --output="%SOURCE_ZIP%" HEAD
if %ERRORLEVEL% NEQ 0 exit /b 1
echo [INFO] Injecting archive identity (.translator-update.json)...
"%PYTHON_CMD%" tools\inject_archive_identity.py --zip "%SOURCE_ZIP%"
if %ERRORLEVEL% NEQ 0 exit /b 1
exit /b 0

:write_sha256sums
echo [INFO] Writing SHA256SUMS.txt...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $dir = Resolve-Path -LiteralPath '%RELEASE_DIR%'; Get-ChildItem -LiteralPath $dir -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | Sort-Object Name | ForEach-Object { '{0}  {1}' -f (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant(), $_.Name } | Set-Content -LiteralPath (Join-Path $dir 'SHA256SUMS.txt') -Encoding ASCII"
if %ERRORLEVEL% NEQ 0 exit /b 1
echo [INFO] Writing manual-artifacts.json (these ZIPs are never auto-update assets)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $dir = Resolve-Path -LiteralPath '%RELEASE_DIR%'; $zips = @(Get-ChildItem -LiteralPath $dir -File -Filter '*.zip' | Sort-Object Name | ForEach-Object Name); $payload = [ordered]@{ schema = 1; channel = 'manual'; note = 'Manual distribution ZIPs. The updater never selects these as executable updates.'; files = $zips }; $payload | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dir 'manual-artifacts.json') -Encoding UTF8"
if %ERRORLEVEL% NEQ 0 exit /b 1
exit /b 0
