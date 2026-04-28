@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if "%~1"=="" (
  echo Usage:
  echo   merge_html_chapters.bat "C:\path\to\html-folder"
  echo.
  echo Optional:
  echo   merge_html_chapters.bat "C:\path\to\html-folder" "Book Title"
  exit /b 2
)
set "INPUT_DIR=%~1"
set "BOOK_TITLE=%~2"
if "%BOOK_TITLE%"=="" set "BOOK_TITLE=%~n1"
python "%SCRIPT_DIR%merge_html_chapters.py" "%INPUT_DIR%" --pattern "*_translated_gemini.html" --title "%BOOK_TITLE%" --out "%INPUT_DIR%\combined.html" --epub "%INPUT_DIR%\combined.epub"
pause
