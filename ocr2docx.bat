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
    echo   图片转文字工具  ocr2docx.py
    echo   ------------------------------------------------------
    echo   把扫描件 / 截图 / PDF 识别成 Word 初稿。
    echo.
    echo   用法一：把图片文件夹或 PDF 直接拖到本文件上
    echo   用法二：ocr2docx.bat 图片目录
    echo           ocr2docx.bat 通知.pdf -o 初稿.docx
    echo.
    echo   产出三个文件：
    echo     初稿.docx        排好版的初稿
    echo     初稿.txt         纯文本，便于改完再用 gongwen 重排
    echo     初稿.report.txt  识别情况与存疑行清单
    echo.
    echo   注意：标题层级和表格是自动推断的，文字也未经校订，
    echo         正式使用前必须通读校对一遍。
    echo.
    pause
    exit /b 0
)

echo 正在识别，页数较多时需要几分钟，请稍候……
echo.
"%PY%" "%~dp0ocr2docx.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo.& echo [失败] 返回码 %RC%，请查看上面的提示。
echo.
pause
exit /b %RC%

:err
echo.
pause
exit /b 1
