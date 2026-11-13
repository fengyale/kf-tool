@echo off
setlocal
cd /d "%~dp0"
chcp 936 >nul 2>&1
rem 控制台代码页为 936(GBK)，让 Python 的输出也按 GBK 编码，
rem 否则中文提示在 cmd 窗口里会显示成乱码。
set "PYTHONIOENCODING=gbk"
set "PYTHONUTF8=0"
call "_find_py.bat"
if errorlevel 1 goto :err

if "%~1"=="" (
    echo.
    echo   版权工具  add_copyright.py
    echo   ------------------------------------------------------
    echo   给已有的 Word 文档加上版权归属、版本号和页码。
    echo.
    echo   用法一：把 .docx 文件直接拖到本文件上
    echo   用法二：add_copyright.bat 文档.docx
    echo           add_copyright.bat 文档.docx -o 新文档.docx
    echo           add_copyright.bat 文档.docx --owner "KingFeng" --version "V2.0"
    echo.
    echo   默认归属方 KingFeng、版本号 V1.0，加 --no-copyright 可整个不要。
    echo   详见 README.md 或执行：add_copyright.bat --help
    echo.
    pause
    exit /b 0
)

"%PY%" "%~dp0add_copyright.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo.& echo [失败] 返回码 %RC%，请查看上面的提示。
echo.
pause
exit /b %RC%

:err
echo.
pause
exit /b 1