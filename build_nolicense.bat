@echo off
cd /d "%~dp0"
call build.bat nolicense
exit /b %ERRORLEVEL%
