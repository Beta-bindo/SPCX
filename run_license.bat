@echo off
cd /d "%~dp0"
(
  echo # Local licensed dev - requires auth server approval
  echo LICENSE_REQUIRED = True
)> app\core\build_config.py
python main.py
