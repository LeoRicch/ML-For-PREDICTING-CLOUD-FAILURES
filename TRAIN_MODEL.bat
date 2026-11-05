@echo off
setlocal
cd /d "%~dp0"
if exist "runtime\python.exe" (
  set "LAB_PYTHON=runtime\python.exe"
) else if exist ".venv\Scripts\python.exe" (
  set "LAB_PYTHON=.venv\Scripts\python.exe"
) else (
  echo Use SETUP.bat for the source edition, or extract the entire offline ZIP.
  pause
  exit /b 1
)
"%LAB_PYTHON%" train.py --runs-per-load 200
if errorlevel 1 (
  echo Model preparation failed. Read the error above.
) else (
  echo Model ready. New experiments will use it.
)
pause
