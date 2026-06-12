@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
    echo Run build.bat first to create .venv
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
(
echo [global]
echo index-url = https://pypi.tuna.tsinghua.edu.cn/simple
echo trusted-host = pypi.tuna.tsinghua.edu.cn
) > ".venv\pip.ini"
echo Using Tsinghua mirror...
pip install -r requirements-windows.txt
if errorlevel 1 (
    echo.
    echo Retrying with Aliyun mirror...
    (
    echo [global]
    echo index-url = https://mirrors.aliyun.com/pypi/simple/
    echo trusted-host = mirrors.aliyun.com
    ) > ".venv\pip.ini"
    pip install -r requirements-windows.txt
)
pause
