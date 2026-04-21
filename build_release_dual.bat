@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if not "%~1"=="" set "NON_INTERACTIVE=1"

call :resolve_python
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
if exist "build\translatorFork-translator" rmdir /S /Q "build\translatorFork-translator"
if exist "build\translatorFork-full" rmdir /S /Q "build\translatorFork-full"
exit /b 0

:build_translator
call :prepare_build
if %ERRORLEVEL% NEQ 0 goto :eof
echo.
echo [STEP] Building small translator-only release...
"%PYTHON_CMD%" -m PyInstaller --clean --noconfirm "translatorFork-translator-only.spec"
call :finish_build "dist\translatorFork-translator.exe"
goto :eof

:build_full
call :prepare_build
if %ERRORLEVEL% NEQ 0 goto :eof
echo.
echo [STEP] Building full release...
"%PYTHON_CMD%" -m PyInstaller --clean --noconfirm "translatorFork-full.spec"
call :finish_build "dist\translatorFork-full.exe"
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
call :finish_build "dist\translatorFork-translator.exe" "dist\translatorFork-full.exe"
goto :eof

:finish_build
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Build failed.
    if not defined NON_INTERACTIVE pause
    exit /b 1
)

echo.
echo [OK] Build finished.
if not "%~1"=="" echo     %~1
if not "%~2"=="" echo     %~2
if not defined NON_INTERACTIVE pause
exit /b 0
