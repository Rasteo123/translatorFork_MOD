@echo off
chcp 65001 >nul
setlocal
cls
goto setup_env

:: ============================================================================
:: Универсальный лаунчер GeminiTranslator
:: Сгенерировано: build_master.py (v15.0 - "The Universal Collector")
:: ============================================================================

:: --- Этап 1: Проверка и запрос прав администратора (если нужно) ---
>nul 2>&1 net session
if '%errorlevel%' NEQ '0' (
    echo.
    echo [+] Запрос прав администратора...
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~f0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    cscript "%temp%\getadmin.vbs" & exit /B
)
if exist "%temp%\getadmin.vbs" ( del "%temp%\getadmin.vbs" )

:: --- Этап 2: Настройка рабочего окружения ---
:setup_env
cd /d "%~dp0"
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
        echo [!!!] Python не найден. Установите Python или создайте локальный .venv рядом с проектом.
        pause
        goto :eof
    )
) else if not exist "%PYTHON_CMD%" (
    echo [!!!] Не найден интерпретатор Python: %PYTHON_CMD%
    pause
    goto :eof
)

echo [+] Используется Python: %PYTHON_CMD%
echo [+] Рабочая директория: %cd%

for %%I in ("%cd%") do set "AppName=%%~nxI"
echo [+] Имя приложения будет: %AppName%
echo.

:: --- Главное меню ---
:menu
cls
echo ======================================================
echo   Универсальный лаунчер для: %AppName%
echo ======================================================
echo.
echo   1. Установить / Обновить зависимости программы
echo.
echo   2. Собрать приложение
echo.
echo   3. Выход
echo.
echo ======================================================
set /p choice="Выберите действие (1, 2 или 3): "

if not defined choice ( goto menu )
if "%choice%"=="1" ( goto install_deps )
if "%choice%"=="2" ( goto build_menu )
if "%choice%"=="3" ( goto :eof )

echo Неверный выбор. Пожалуйста, введите 1, 2 или 3.
pause
goto menu

:: --- Меню сборки ---
:build_menu
cls
echo ======================================================
echo   Выберите тип сборки
echo ======================================================
echo.
echo   1. ПОЛНОСТЬЮ ПОРТАТИВНАЯ (один .exe файл)
echo      - Создает один .exe файл. Все встроено внутрь.
echo      - Легко распространять, но настройки менять нельзя.
echo      - Рекомендуется для большинства пользователей.
echo.
echo   2. ГИБРИДНАЯ (один .exe + папки с данными)
echo      - Создает один .exe и рядом с ним папки с данными.
echo      - Сочетает портативность и возможность менять конфиги.
echo      - Рекомендуется для опытных пользователей.
echo.
echo   3. ПРОДВИНУТАЯ (папка с файлами)
echo      - Создает папку с .exe и всеми зависимостями.
echo      - Позволяет вручную редактировать конфиги и данные.
echo      - Для разработчиков и отладки.
echo.
echo   4. Назад в главное меню
echo.
echo ======================================================
set /p build_choice="Выберите действие (1, 2, 3 или 4): "

if not defined build_choice ( goto build_menu )
if "%build_choice%"=="1" ( goto build_full_portable )
if "%build_choice%"=="2" ( goto build_hybrid )
if "%build_choice%"=="3" ( goto build_advanced )
if "%build_choice%"=="4" ( goto menu )

echo Неверный выбор.
pause
goto build_menu


:: --- Блок установки зависимостей ---
:install_deps
cls
echo --- Установка / обновление зависимостей программы ---
echo.
echo [+] Запуск установки из файла 'requirements.txt'...
"%PYTHON_CMD%" -m pip install --upgrade -r "requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo [!!!] Ошибка при установке. Проверьте подключение к интернету.
) else (
    echo [OK] Все зависимости успешно установлены/обновлены.
)
echo.
pause
goto :eof


:: --- Блок сборки: ПОЛНОСТЬЮ ПОРТАТИВНАЯ ---
:build_full_portable
call :build_app_base "ПОЛНОСТЬЮ ПОРТАТИВНАЯ"
"%PYTHON_CMD%" -m PyInstaller main.py ^
--windowed ^
--name="%AppName%" ^
--clean ^
--icon="gemini_translator\GT.ico" ^
--noconfirm ^
--collect-data="PyQt6" ^
--collect-data="docx" ^
--collect-data="emoji" ^
--collect-data="jieba" ^
--collect-data="lxml" ^
--collect-data="werkzeug" ^
--hidden-import="PyQt6.sip" ^
--hidden-import="docx" ^
--hidden-import="playwright.sync_api" ^
--hidden-import="google.genai" ^
--hidden-import="google.genai.types" ^
--onefile ^
--add-data "config;config" ^
--add-data "README.md;." ^
--add-data "ffmpeg.exe;." ^
--add-data "ffprobe.exe;." ^
--add-data "gemini_translator\scripts\chatgpt_workascii_bridge.cjs;gemini_translator\scripts" ^
--add-data "gemini_translator\scripts\chatgpt_profile_launcher.cjs;gemini_translator\scripts" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\__init__.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\api_upload.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\constants.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\dependencies.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\dialogs.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\main.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\main_window.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\models.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\parsers.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\ranobelib-upload.mjs;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\ranobelib_uploader_v12.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\utils.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\workers.py;ranobelib" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\node.exe;playwright_runtime" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\package;playwright_runtime\package" ^
--add-data "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\ms-playwright;playwright_runtime\ms-playwright"
call :build_app_end
goto :eof


:: --- Блок сборки: ГИБРИДНАЯ ---
:build_hybrid
call :build_app_base "ГИБРИДНАЯ"
"%PYTHON_CMD%" -m PyInstaller main.py ^
--windowed ^
--name="%AppName%" ^
--clean ^
--icon="gemini_translator\GT.ico" ^
--noconfirm ^
--collect-data="PyQt6" ^
--collect-data="docx" ^
--collect-data="emoji" ^
--collect-data="jieba" ^
--collect-data="lxml" ^
--collect-data="werkzeug" ^
--hidden-import="PyQt6.sip" ^
--hidden-import="docx" ^
--hidden-import="playwright.sync_api" ^
--hidden-import="google.genai" ^
--hidden-import="google.genai.types" ^
--onefile
if %ERRORLEVEL% EQU 0 (
    echo.
    echo [+] Этап 3 из 3: Копирование внешних данных...
    xcopy "config" "dist\config\" /E /I /Y /Q > nul
    copy /Y "README.md" "dist\README.md" > nul
    copy /Y "ffmpeg.exe" "dist\ffmpeg.exe" > nul
    copy /Y "ffprobe.exe" "dist\ffprobe.exe" > nul
    if not exist "dist\gemini_translator\scripts" mkdir "dist\gemini_translator\scripts"
    copy /Y "gemini_translator\scripts\chatgpt_workascii_bridge.cjs" "dist\gemini_translator\scripts\chatgpt_workascii_bridge.cjs" > nul
    if not exist "dist\gemini_translator\scripts" mkdir "dist\gemini_translator\scripts"
    copy /Y "gemini_translator\scripts\chatgpt_profile_launcher.cjs" "dist\gemini_translator\scripts\chatgpt_profile_launcher.cjs" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\__init__.py" "dist\ranobelib\__init__.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\api_upload.py" "dist\ranobelib\api_upload.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\constants.py" "dist\ranobelib\constants.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\dependencies.py" "dist\ranobelib\dependencies.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\dialogs.py" "dist\ranobelib\dialogs.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\main.py" "dist\ranobelib\main.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\main_window.py" "dist\ranobelib\main_window.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\models.py" "dist\ranobelib\models.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\parsers.py" "dist\ranobelib\parsers.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\ranobelib-upload.mjs" "dist\ranobelib\ranobelib-upload.mjs" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\ranobelib_uploader_v12.py" "dist\ranobelib\ranobelib_uploader_v12.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\utils.py" "dist\ranobelib\utils.py" > nul
    if not exist "dist\ranobelib" mkdir "dist\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\workers.py" "dist\ranobelib\workers.py" > nul
    if not exist "dist\playwright_runtime" mkdir "dist\playwright_runtime"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\node.exe" "dist\playwright_runtime\node.exe" > nul
    xcopy "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\package" "dist\playwright_runtime\package\" /E /I /Y /Q > nul
    xcopy "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\ms-playwright" "dist\playwright_runtime\ms-playwright\" /E /I /Y /Q > nul
    echo [OK] Данные скопированы.
)
call :build_app_end
goto :eof


:: --- Блок сборки: ПРОДВИНУТАЯ ---
:build_advanced
call :build_app_base "ПРОДВИНУТАЯ"
"%PYTHON_CMD%" -m PyInstaller main.py ^
--windowed ^
--name="%AppName%" ^
--clean ^
--icon="gemini_translator\GT.ico" ^
--noconfirm ^
--collect-data="PyQt6" ^
--collect-data="docx" ^
--collect-data="emoji" ^
--collect-data="jieba" ^
--collect-data="lxml" ^
--collect-data="werkzeug" ^
--hidden-import="PyQt6.sip" ^
--hidden-import="docx" ^
--hidden-import="playwright.sync_api" ^
--hidden-import="google.genai" ^
--hidden-import="google.genai.types"
if %ERRORLEVEL% EQU 0 (
    echo.
    echo [+] Этап 3 из 3: Копирование внешних данных...
    xcopy "config" "dist\%AppName%\config\" /E /I /Y /Q > nul
    copy /Y "README.md" "dist\%AppName%\README.md" > nul
    copy /Y "ffmpeg.exe" "dist\%AppName%\ffmpeg.exe" > nul
    copy /Y "ffprobe.exe" "dist\%AppName%\ffprobe.exe" > nul
    if not exist "dist\%AppName%\gemini_translator\scripts" mkdir "dist\%AppName%\gemini_translator\scripts"
    copy /Y "gemini_translator\scripts\chatgpt_workascii_bridge.cjs" "dist\%AppName%\gemini_translator\scripts\chatgpt_workascii_bridge.cjs" > nul
    if not exist "dist\%AppName%\gemini_translator\scripts" mkdir "dist\%AppName%\gemini_translator\scripts"
    copy /Y "gemini_translator\scripts\chatgpt_profile_launcher.cjs" "dist\%AppName%\gemini_translator\scripts\chatgpt_profile_launcher.cjs" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\__init__.py" "dist\%AppName%\ranobelib\__init__.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\api_upload.py" "dist\%AppName%\ranobelib\api_upload.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\constants.py" "dist\%AppName%\ranobelib\constants.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\dependencies.py" "dist\%AppName%\ranobelib\dependencies.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\dialogs.py" "dist\%AppName%\ranobelib\dialogs.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\main.py" "dist\%AppName%\ranobelib\main.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\main_window.py" "dist\%AppName%\ranobelib\main_window.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\models.py" "dist\%AppName%\ranobelib\models.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\parsers.py" "dist\%AppName%\ranobelib\parsers.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\ranobelib-upload.mjs" "dist\%AppName%\ranobelib\ranobelib-upload.mjs" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\ranobelib_uploader_v12.py" "dist\%AppName%\ranobelib\ranobelib_uploader_v12.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\utils.py" "dist\%AppName%\ranobelib\utils.py" > nul
    if not exist "dist\%AppName%\ranobelib" mkdir "dist\%AppName%\ranobelib"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\ranobelib\workers.py" "dist\%AppName%\ranobelib\workers.py" > nul
    if not exist "dist\%AppName%\playwright_runtime" mkdir "dist\%AppName%\playwright_runtime"
    copy /Y "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\node.exe" "dist\%AppName%\playwright_runtime\node.exe" > nul
    xcopy "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\package" "dist\%AppName%\playwright_runtime\package\" /E /I /Y /Q > nul
    xcopy "C:\Users\shest\Downloads\rulate\translatorFork 1.1\playwright_runtime\ms-playwright" "dist\%AppName%\playwright_runtime\ms-playwright\" /E /I /Y /Q > nul
    echo [OK] Данные скопированы.
)
call :build_app_end
goto :eof


:: --- Общая логика сборки ---
:build_app_base
cls
echo --- Полный цикл сборки (%~1 версия) ---
echo.
echo [+] Этап 1 из 3: Установка/обновление всех зависимостей и инструментов...
"%PYTHON_CMD%" -m pip install --upgrade -r "requirements.txt" pyinstaller pyinstaller-hooks-contrib
if %ERRORLEVEL% NEQ 0 (
    echo [!!!] Ошибка при установке зависимостей. Проверьте подключение к интернету.
    pause
    goto menu
)
if exist "dist\chatgpt-profile-run" rmdir /S /Q "dist\chatgpt-profile-run"
if exist "dist\logs" rmdir /S /Q "dist\logs"
if exist "dist\%AppName%\chatgpt-profile-run" rmdir /S /Q "dist\%AppName%\chatgpt-profile-run"
if exist "dist\%AppName%\logs" rmdir /S /Q "dist\%AppName%\logs"
echo [+] Инструменты для сборки готовы.
echo.
echo [+] Этап 2 из 3: Запуск PyInstaller для сборки "%AppName%"...
echo.
goto :eof

:build_app_end
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!!!] СБОРКА ЗАВЕРШИЛАСЬ С ОШИБКОЙ!
    echo     Просмотрите сообщения выше, чтобы найти причину.
) else (
    echo.
    echo [OK] СБОРКА УСПЕШНО ЗАВЕРШЕНА!
    echo     Готовое приложение находится в папке 'dist'.
)
echo.
echo [+] Процесс завершен.
pause
goto :eof

