@echo off
setlocal

cd /d "%~dp0"
set "VENV_DIR=%cd%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%cd%\requirements.txt"
set "REQ_STAMP=%VENV_DIR%\.requirements.sha256"

if not exist "%VENV_PYTHON%" (
    echo [+] Creating local virtual environment...
    python -m venv "%VENV_DIR%"
    if %ERRORLEVEL% NEQ 0 exit /b 1

    echo [+] Upgrading pip...
    "%VENV_PYTHON%" -m pip install --upgrade pip
    if %ERRORLEVEL% NEQ 0 exit /b 1
)

call :sync_requirements
if %ERRORLEVEL% NEQ 0 exit /b 1

"%VENV_PYTHON%" "main.py"
exit /b %ERRORLEVEL%

:sync_requirements
if not exist "%REQ_FILE%" exit /b 0

set "REQ_HASH="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath '%REQ_FILE%').Hash"`) do set "REQ_HASH=%%H"
if not defined REQ_HASH (
    echo [!] Failed to calculate requirements hash.
    exit /b 1
)

set "CURRENT_HASH="
if exist "%REQ_STAMP%" set /p CURRENT_HASH=<"%REQ_STAMP%"
if /I "%CURRENT_HASH%"=="%REQ_HASH%" exit /b 0

echo [+] Syncing project dependencies...
"%VENV_PYTHON%" -m pip install --upgrade -r "%REQ_FILE%"
if %ERRORLEVEL% NEQ 0 exit /b 1

>"%REQ_STAMP%" echo %REQ_HASH%
exit /b 0
