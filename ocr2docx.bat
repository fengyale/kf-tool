@echo off
setlocal
cd /d "%~dp0"
chcp 936 >nul 2>&1
rem Console code page 936 (GBK) so Python's Chinese output renders correctly.
rem Keep this file pure ASCII: cmd.exe parses .bat bytes with the ANSI code page.
set "PYTHONIOENCODING=gbk"
set "PYTHONUTF8=0"
call "_find_py.bat"
if errorlevel 1 goto :err

if "%~1"=="" (
    echo.
    echo   ocr2docx.py - convert images / PDFs into a Word draft
    echo   ------------------------------------------------------
    echo   OCR scans, screenshots or PDFs into an editable .docx draft.
    echo.
    echo   Usage 1: drag an image folder or a PDF onto this file
    echo   Usage 2: ocr2docx.bat images_dir
    echo            ocr2docx.bat notice.pdf -o draft.docx
    echo.
    echo   Outputs three files:
    echo     draft.docx        formatted draft
    echo     draft.txt         plain text, edit then re-run gongwen
    echo     draft.report.txt  per-page stats and uncertain lines
    echo.
    echo   Note: heading levels and tables are inferred, text is not proofread.
    echo         Always review the draft before using it.
    echo.
    pause
    exit /b 0
)

echo Recognizing text, this may take minutes for many pages...
echo.
"%PY%" "%~dp0ocr2docx.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo.& echo [ERROR] exit code %RC%, see messages above.
echo.
pause
exit /b %RC%

:err
echo.
pause
exit /b 1
