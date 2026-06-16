@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "NONINTERACTIVE=1"

echo ========================================
echo   Build ALL 3 packages
echo ========================================

call build.bat license
if errorlevel 1 (
  echo [ERROR] License build failed.
  exit /b 1
)

call build.bat release
if errorlevel 1 (
  echo [ERROR] Release build failed.
  exit /b 1
)

call build.bat nolicense
if errorlevel 1 (
  echo [ERROR] No-license build failed.
  exit /b 1
)

echo.
echo ========================================
echo   ALL 3 BUILDS OK
echo   dist\TradeAssistant.exe          (授权 + 仅实盘 BA+MT5)
echo   dist\TradeAssistant-license.exe  (授权 + 全部连接模式)
echo   dist\TradeAssistant-nolicense.exe (无授权)
echo   dist\releases\
echo ========================================
endlocal
exit /b 0
