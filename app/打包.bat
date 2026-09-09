@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Build  guiyuan-ledger  (PyInstaller)
echo ============================================

if not exist ".venv\Scripts\python.exe" (
  echo [error] .venv not found. Run 啟動.bat once first to create it.
  pause & exit /b 1
)
set "VPY=.venv\Scripts\python.exe"

echo [1/2] installing pyinstaller ...
"%VPY%" -m pip install --quiet --disable-pip-version-check pyinstaller
if errorlevel 1 ( echo [error] pip install failed. & pause & exit /b 1 )

echo [2/2] building ... (takes 1-3 minutes)
"%VPY%" -m PyInstaller --noconfirm --clean --onedir --console --name 桂圓帳房 ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "schema.sql;." ^
  --hidden-import seed ^
  --collect-submodules uvicorn ^
  launch.py
if errorlevel 1 ( echo [error] build failed. & pause & exit /b 1 )

echo.
echo ============================================
echo   DONE.  Output folder:  dist\桂圓帳房\
echo.
echo   Give the whole  桂圓帳房  folder to 家易
echo   (zip it first).  He double-clicks
echo   桂圓帳房.exe  inside.  No Python needed.
echo ============================================
pause
