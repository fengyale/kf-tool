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
    echo   add_copyright.py - add copyright, version and page numbers to a .docx
    echo   ------------------------------------------------------
    echo   Leaves body text, tables and paragraph formatting untouched.
    echo.
    echo   Usage 1: drag a .docx file onto this file
    echo   Usage 2: add_copyright.bat doc.docx
    echo            add_copyright.bat doc.docx -o new.docx
    echo            add_copyright.bat doc.docx --owner "Owner" --version "V2.0"
    echo.
    echo   Default owner is empty and version is V1.0.
    echo   See README.md, or run: add_copyright.bat --help
    echo.
    pause
    exit /b 0
)

"%PY%" "%~dp0add_copyright.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo.& echo [ERROR] exit code %RC%, see messages above.
echo.
pause
exit /b %RC%

:err
echo.
pause
exit /b 1
