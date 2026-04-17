@echo off
chcp 65001 >nul
setlocal
cls

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
cd /d "%~dp0"
echo [+] Рабочая директория: %cd%

for %%I in ("%cd%") do set "AppName=Text Unifier"
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
echo   0. Назад в главное меню
echo.
echo ======================================================
set /p build_choice="Выберите действие (1 или 0): "

if not defined build_choice ( goto build_menu )
if "%build_choice%"=="1" ( goto build_full_portable )
if "%build_choice%"=="0" ( goto menu )

echo Неверный выбор.
pause
goto build_menu


:: --- Блок установки зависимостей ---
:install_deps
cls
echo --- Установка / обновление зависимостей программы ---
echo.
echo [+] Запуск установки из файла 'requirements.txt'...
pip install --upgrade -r "requirements.txt"
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
pyinstaller text_unifier.py ^
--windowed ^
--name="%AppName%" ^
--clean ^
--icon="gemini_translator\GT.ico" ^
--noconfirm ^
--collect-data="PyQt6" ^
--collect-data="jieba" ^
--collect-data="lxml" ^
--collect-data="setuptools" ^
--collect-data="werkzeug" ^
--hidden-import="PyQt6.sip" ^
--onefile
call :build_app_end
goto :eof


:: --- ОБЩАЯ ЛОГИКА СБОРКИ ---
:build_app_base
cls
echo --- Полный цикл сборки (%~1 версия) ---
echo.
echo [+] Этап 1 из 3: Установка/обновление всех зависимостей и инструментов...
pip install --upgrade -r "requirements.txt" pyinstaller pyinstaller-hooks-contrib
if %ERRORLEVEL% NEQ 0 (
    echo [!!!] Ошибка при установке зависимостей. Проверьте подключение к интернету.
    pause
    goto menu
)
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
