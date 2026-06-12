@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   Trade Assistant - Inno Setup Installer
echo ========================================

if not exist "dist\TradeAssistant.exe" (
    if exist "%TEMP%\TradeAssistant_build_out\TradeAssistant.exe" (
        if not exist "dist" mkdir "dist"
        copy /Y "%TEMP%\TradeAssistant_build_out\TradeAssistant.exe" "dist\TradeAssistant.exe" >nul
    )
)
if not exist "dist\TradeAssistant.exe" (
    echo [ERROR] Run build.bat first.
    pause
    exit /b 1
)

set "ISCC="
for %%I in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) do (
    if exist %%I set "ISCC=%%~I"
)

if "%ISCC%"=="" (
    echo [ERROR] Inno Setup 6 not found.
    echo Download: https://jrsoftware.org/isinfo.php
    pause
    exit /b 1
)

"%ISCC%" installer\setup.iss
if errorlevel 1 (
    echo [ERROR] Installer build failed.
    pause
    exit /b 1
)

echo.
echo OK: installer_output\TradeAssistant_Setup_1.0.0.exe
pause
endlocal
