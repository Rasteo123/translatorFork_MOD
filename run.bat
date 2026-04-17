@echo off
setlocal

cd /d "%~dp0"
set "VENV_DIR=%cd%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [+] Creating local virtual environment...
    python -m venv "%VENV_DIR%"
    if %ERRORLEVEL% NEQ 0 exit /b 1

    echo [+] Upgrading pip...
    "%VENV_PYTHON%" -m pip install --upgrade pip
    if %ERRORLEVEL% NEQ 0 exit /b 1

    echo [+] Installing project dependencies...
    "%VENV_PYTHON%" -m pip install --upgrade -r "requirements.txt"
    if %ERRORLEVEL% NEQ 0 exit /b 1
)

"%VENV_PYTHON%" "main.py"
