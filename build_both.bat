@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "NONINTERACTIVE=1"

echo ========================================
echo   Build BOTH: license + nolicense
echo ========================================

call build.bat license
if errorlevel 1 (
  echo [ERROR] License build failed.
  exit /b 1
)

call build.bat nolicense
if errorlevel 1 (
  echo [ERROR] No-license build failed.
  exit /b 1
)

echo.
echo ========================================
echo   BOTH BUILDS OK
echo   dist\TradeAssistant-license.exe
echo   dist\TradeAssistant-nolicense.exe
echo   dist\releases\
echo ========================================
endlocal
exit /b 0
