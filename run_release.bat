@echo off
cd /d "%~dp0"
(
  echo # Local release dev - licensed, live BA+MT5 only
  echo LICENSE_REQUIRED = True
  echo LIVE_BOTH_ONLY = True
)> app\core\build_config.py
python main.py
