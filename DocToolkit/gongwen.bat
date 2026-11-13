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
    echo   gongwen.py - format Chinese official documents into Word
    echo   ------------------------------------------------------
    echo   Write the body as .txt, run one command, get a formatted .docx.
    echo.
    echo   Usage 1: drag a .txt file onto this file
    echo   Usage 2: gongwen.bat body.txt
    echo            gongwen.bat body.txt -o notice.docx
    echo            gongwen.bat body.txt --title "Title" --subtitle "(Draft)"
    echo.
    echo   Markup and options: see README.md, or run: gongwen.bat --help
    echo.
    pause
    exit /b 0
)

"%PY%" "%~dp0gongwen.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo.& echo [ERROR] exit code %RC%, see messages above.
echo.
pause
exit /b %RC%

:err
echo.
pause
exit /b 1
