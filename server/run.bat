@echo off
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
if not exist .env (
  copy .env.example .env >nul
  echo Created .env from .env.example.
  echo Please set TA_ADMIN_PASSWORD_HASH and TA_JWT_SECRET before starting the server.
  echo Generate password hash with: .venv\Scripts\python scripts\hash_admin_password.py
  exit /b 2
)
setlocal
set TA_ADMIN_PASSWORD_HASH=
set TA_ADMIN_PASSWORD=
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
  set "%%A=%%B"
)
echo License server starting on http://127.0.0.1:%TA_PORT%
echo Admin page: http://127.0.0.1:%TA_PORT%/admin
.venv\Scripts\uvicorn app.main:app --host %TA_HOST% --port %TA_PORT% --reload
