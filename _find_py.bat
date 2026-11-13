@echo off
rem ---- locate a usable Python interpreter ----
rem This file is called by other .bat files, so no setlocal here:
rem the PY variable must survive after this script returns.
rem
rem Lookup order:
rem   1) python_path.txt next to this file (one line, full path to python.exe).
rem      Local config only, git-ignored.
rem   2) py / python / python3 on PATH - prefer the one that can import docx.
set "PY="
set "PY_ANY="
set "PY_NO_DOCX="

if exist "%~dp0python_path.txt" (
    for /f "usebackq delims=" %%L in ("%~dp0python_path.txt") do (
        if not defined PY if exist "%%L" set "PY=%%L"
    )
    if defined PY goto :done
)

for %%C in (py.exe python.exe python3.exe) do (
    if not defined PY (
        for /f "delims=" %%L in ('where %%C 2^>nul') do (
            if not defined PY call :probe "%%L"
        )
    )
)

if not defined PY if defined PY_ANY (
    set "PY=%PY_ANY%"
    set "PY_NO_DOCX=1"
)

:done
if not defined PY (
    echo [ERROR] Python not found. Install Python 3.9 or newer.
    echo         Download: https://www.python.org/downloads/
    echo         Check "Add Python to PATH" during installation.
    echo         Or create python_path.txt here with the full path to python.exe.
    exit /b 1
)
if defined PY_NO_DOCX (
    echo [WARN] This Python may lack dependencies. If you see "No module named ...", run:
    echo        "%PY%" -m pip install -r "%~dp0requirements.txt"
    echo.
)
exit /b 0

:probe
"%~1" -c "import docx" >nul 2>&1
if errorlevel 1 (
    if not defined PY_ANY set "PY_ANY=%~1"
) else (
    if not defined PY set "PY=%~1"
)
goto :eof
