@echo off
setlocal EnableDelayedExpansion
goto :main

:log
echo %*
>>"%LOG%" echo %*
exit /b 0

:runstep
set "TITLE=%_RUN_TITLE%"
set "STEP_TMP=%TEMP%\ta_build_%RANDOM%.txt"
if exist "!STEP_TMP!" del /f /q "!STEP_TMP!"
echo.
>>"%LOG%" echo.
call :log ========== %TITLE% ==========
echo.
echo ========== %TITLE% ==========
echo Running, please wait...
>>"%LOG%" echo --- output ---
cmd /c "!_RUN_CMD!" >> "!STEP_TMP!" 2>&1
set "RC=!ERRORLEVEL!"
if exist "!STEP_TMP!" (
  type "!STEP_TMP!"
  type "!STEP_TMP!" >> "%LOG%"
  del /f /q "!STEP_TMP!"
)
call :log Exit code: !RC!
if !RC! neq 0 call :log [ERROR] Step failed: %TITLE% (exit !RC!)
exit /b !RC!

:write_pip_ini
(
echo [global]
echo index-url = %MIRROR_URL%
echo trusted-host = %MIRROR_HOST%
) > "%~dp0.venv\pip.ini"
call :log pip.ini -^> %MIRROR_HOST%
exit /b 0

:prepare_pack
call :log Stop running TradeAssistant.exe if any...
taskkill /F /IM TradeAssistant.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul
if exist "build_tmp" rmdir /s /q "build_tmp" 2>nul
if exist "%OUT_DIR%" rmdir /s /q "%OUT_DIR%" 2>nul
mkdir "%OUT_DIR%" 2>nul
call :log Output dir: %OUT_DIR%
exit /b 0

:copy_to_dist
if not exist "%OUT_DIR%\TradeAssistant.exe" exit /b 1
if not exist "dist" mkdir "dist"
if not exist "dist\releases" mkdir "dist\releases"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "BUILD_STAMP=%%i"
if /i "%BUILD_MODE%"=="nolicense" (
  set "ARCHIVE_NAME=TradeAssistant-nolicense-!BUILD_STAMP!.exe"
  set "LATEST_ALIAS=TradeAssistant-nolicense.exe"
) else (
  set "ARCHIVE_NAME=TradeAssistant-license-!BUILD_STAMP!.exe"
  set "LATEST_ALIAS=TradeAssistant-license.exe"
)

copy /Y "%OUT_DIR%\TradeAssistant.exe" "dist\releases\!ARCHIVE_NAME!" >nul
if errorlevel 1 (
  call :log [ERROR] Failed to archive build to dist\releases\
  exit /b 1
)
call :log Archived: dist\releases\!ARCHIVE_NAME!

copy /Y "%OUT_DIR%\TradeAssistant.exe" "dist\!LATEST_ALIAS!" >nul
if /i "%BUILD_MODE%"=="license" (
  copy /Y "%OUT_DIR%\TradeAssistant.exe" "dist\TradeAssistant.exe" >nul
  call :log Latest: dist\TradeAssistant.exe
)
call :log Latest: dist\!LATEST_ALIAS!
exit /b 0

:pack_hints
echo.
call :log [HINT] Permission denied on TradeAssistant.exe
call :log [HINT] 1. Close TradeAssistant.exe
call :log [HINT] 2. Close File Explorer on dist\ or out\
call :log [HINT] 3. Add project folder to antivirus exclusion
call :log [HINT] 4. Run build.bat again
goto :fail

:main
rem build.bat v8 - licensed commercial build
cd /d "%~dp0"

set "BUILD_MODE=license"
if /i "%~1"=="nolicense" set "BUILD_MODE=nolicense"
if /i "%BUILD_MODE%"=="nolicense" (
  echo LICENSE_REQUIRED = False> app\core\build_config.py
) else (
  echo LICENSE_REQUIRED = True> app\core\build_config.py
)

set "LOG=build_log.txt"
set "OUT_DIR=%TEMP%\TradeAssistant_build_out"

echo [%date% %time%] build start > "%LOG%"
echo build.bat v8 mode=%BUILD_MODE% >> "%LOG%"

echo ========================================
echo   Trade Assistant - Windows Build v8
echo ========================================
if /i "%BUILD_MODE%"=="nolicense" (
  echo   Mode: nolicense - no auth gate
) else (
  echo   Mode: license - requires auth server
)
echo Work dir: %CD%
echo Build output: %OUT_DIR%
echo Log file: %LOG%
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY where python3 >nul 2>nul && set "PY=python3"

if not defined PY (
    call :log [ERROR] Python not found.
    call :log Install Python 3.10+ from https://www.python.org/downloads/
    goto :fail
)

call :log Using: %PY%

set "_RUN_TITLE=Python version"
set "_RUN_CMD=%PY% --version"
call :runstep
if errorlevel 1 goto :fail

if not exist .venv (
    set "_RUN_TITLE=Create virtual environment"
    set "_RUN_CMD=%PY% -m venv .venv"
    call :runstep
    if errorlevel 1 goto :fail
)

if not exist ".venv\Scripts\activate.bat" (
    call :log [ERROR] Incomplete venv. Delete .venv folder and retry.
    goto :fail
)

call .venv\Scripts\activate.bat
if errorlevel 1 goto :venv_fail
goto :after_venv

:venv_fail
call :log [ERROR] Failed to activate venv.
goto :fail

:after_venv
set "MIRROR_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "MIRROR_HOST=pypi.tuna.tsinghua.edu.cn"
call :write_pip_ini

set "_RUN_TITLE=Upgrade pip"
set "_RUN_CMD=python -m pip install --upgrade pip"
call :runstep
if errorlevel 1 goto :fail

set "_RUN_TITLE=Install requirements"
set "_RUN_CMD=pip install -r requirements-windows.txt"
call :runstep
if errorlevel 1 goto :mirror_retry
goto :after_pip

:mirror_retry
call :log Tsinghua failed, switch to Aliyun mirror...
set "MIRROR_URL=https://mirrors.aliyun.com/pypi/simple/"
set "MIRROR_HOST=mirrors.aliyun.com"
call :write_pip_ini
set "_RUN_TITLE=Install requirements (Aliyun)"
set "_RUN_CMD=pip install -r requirements-windows.txt"
call :runstep
if errorlevel 1 goto :pip_fail
goto :after_pip

:pip_fail
call :log [ERROR] pip install failed. Check network or VPN.
goto :fail

:after_pip
echo.
>>"%LOG%" echo.
call :log ========== Prepare SSL cert bundle ==========
set "_RUN_TITLE=Copy certifi cacert.pem"
set "_RUN_CMD=python scripts\copy_cacert.py"
call :runstep
if errorlevel 1 goto :fail

echo.
>>"%LOG%" echo.
call :log ========== Clean release dist ==========
set "_RUN_TITLE=Remove sensitive sidecar files from dist"
set "_RUN_CMD=python scripts\clean_release_dist.py"
call :runstep
if errorlevel 1 goto :fail

echo.
>>"%LOG%" echo.
call :log ========== Prepare pack ==========
call :prepare_pack
if errorlevel 1 goto :fail

set "_RUN_TITLE=Pre-release checks (tests/memory)"
set "_RUN_CMD=python scripts\pre_release_check.py && python -m pytest tests/ -q --tb=line"
call :runstep
if errorlevel 1 goto :fail

set "_RUN_TITLE=PyInstaller pack (5-15 min)"
set "_RUN_CMD=python -m PyInstaller build.spec --noconfirm --clean --distpath %OUT_DIR% --workpath build_tmp"
call :runstep
if errorlevel 1 goto :pack_hints

if not exist "%OUT_DIR%\TradeAssistant.exe" (
    call :log [ERROR] TradeAssistant.exe not found in %OUT_DIR%
    goto :fail
)

set "_RUN_TITLE=Verify exe contains no BA/Exness credentials"
set "_RUN_CMD=python scripts\verify_release_exe.py %OUT_DIR%\TradeAssistant.exe"
call :runstep
if errorlevel 1 goto :fail

call :copy_to_dist

echo.
>>"%LOG%" echo.
call :log ========================================
call :log BUILD OK
call :log Output: %OUT_DIR%\TradeAssistant.exe
call :log History kept under: dist\releases\
if exist "dist\TradeAssistant.exe" call :log Latest: dist\TradeAssistant.exe
if exist "dist\TradeAssistant-license.exe" call :log Latest: dist\TradeAssistant-license.exe
call :log ========================================
goto :done

:fail
echo.
>>"%LOG%" echo.
call :log ========== BUILD FAILED ==========
call :log Full log: %LOG%
call :log ==================================
if /I not "%NONINTERACTIVE%"=="1" pause
exit /b 1

:done
if /I not "%NONINTERACTIVE%"=="1" pause
endlocal
exit /b 0
