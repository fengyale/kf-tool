@echo off
rem ---- 找一个能用的 Python ----
rem 注意：本文件由其它 bat 用 call 引入，故不能写 setlocal，
rem       否则 PY 变量出了本文件就丢了。
rem
rem 查找顺序：
rem   1) 同目录的 python_path.txt（一行，写 python.exe 的完整路径）。本地配置，不入库。
rem   2) PATH 上的 py / python / python3 —— 优先挑能 import docx 的那个。
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
    echo [错误] 找不到 Python，请先安装 Python 3.9 或更高版本。
    echo        下载地址：https://www.python.org/downloads/
    echo        安装时请勾选 "Add Python to PATH"。
    echo        或者在本目录新建 python_path.txt，写一行 python.exe 的完整路径。
    exit /b 1
)
if defined PY_NO_DOCX (
    echo [提示] 当前 Python 可能没装依赖，若报 "No module named ..." 请执行：
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