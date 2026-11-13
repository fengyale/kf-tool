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
    echo   公文排版工具  gongwen.py
    echo   ------------------------------------------------------
    echo   把正文写成 .txt，一条命令排成规范 Word。
    echo.
    echo   用法一：把 .txt 文件直接拖到本文件上
    echo   用法二：gongwen.bat 正文.txt
    echo           gongwen.bat 正文.txt -o 通知.docx
    echo           gongwen.bat 正文.txt --title "关于XX的通知" --subtitle "（试行）"
    echo.
    echo   标记写法见 README.md 或执行：gongwen.bat --help
    echo.
    pause
    exit /b 0
)

"%PY%" "%~dp0gongwen.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo.& echo [失败] 返回码 %RC%，请查看上面的提示。
echo.
pause
exit /b %RC%

:err
echo.
pause
exit /b 1
