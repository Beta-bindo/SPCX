@echo off
cd /d "%~dp0"
(
  echo # Local nolicense dev - same as build_nolicense output
  echo LICENSE_REQUIRED = False
)> app\core\build_config.py
python main.py
