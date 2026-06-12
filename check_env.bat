@echo off
cd /d "%~dp0"

echo ========================================
echo   Trade Assistant - Environment Check
echo ========================================
echo Dir: %CD%
echo.

echo [1] Python...
set FOUND=0
where py >nul 2>nul && (echo   OK  py & py -3 --version & set FOUND=1)
where python >nul 2>nul && (echo   OK  python & python --version & set FOUND=1)
where python3 >nul 2>nul && (echo   OK  python3 & python3 --version & set FOUND=1)
if "%FOUND%"=="0" (
    echo   [X] Python not found
    echo   Install from https://www.python.org/downloads/
    echo   Check "Add python.exe to PATH"
)

echo.
echo [2] pip...
where pip >nul 2>nul && (echo   OK  pip) || echo   [?] pip not in PATH ^(build.bat uses venv pip^)

echo.
echo [3] Project files...
if exist main.py (echo   OK  main.py) else echo   [X] missing main.py
if exist build.spec (echo   OK  build.spec) else echo   [X] missing build.spec
if exist requirements-windows.txt (echo   OK  requirements-windows.txt) else echo   [X] missing requirements-windows.txt
if exist app\resources\icon.ico (echo   OK  icon.ico) else echo   [X] missing icon.ico

echo.
echo [4] Build output...
if exist dist\TradeAssistant.exe (echo   OK  dist\TradeAssistant.exe) else echo   [ ] exe not built yet

echo.
echo ========================================
echo If no [X], run build.bat
echo On failure see build_log.txt
echo ========================================
pause
