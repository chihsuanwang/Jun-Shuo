@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === Guiyuan Ledger ===

set "PYEXE=py -3.11"
py -3.11 --version >nul 2>nul || set "PYEXE=py"
%PYEXE% --version >nul 2>nul || set "PYEXE=python"

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtual environment with: %PYEXE%
  %PYEXE% -m venv .venv
  if errorlevel 1 (
    echo [error] could not create venv. Is Python installed?
    pause
    exit /b 1
  )
)

set "VPY=.venv\Scripts\python.exe"

echo [setup] installing packages ...
"%VPY%" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 ( echo [error] pip install failed. & pause & exit /b 1 )

if not exist "guiyuan_ledger.db" (
  echo [setup] building database with demo data ...
  "%VPY%" seed.py --force
)

echo.
echo   This computer : http://127.0.0.1:8000
echo   iPad / phone on same Wi-Fi:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /r /c:"IPv4"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do echo       http://%%b:8000
)
echo.
start "" http://127.0.0.1:8000
"%VPY%" -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
