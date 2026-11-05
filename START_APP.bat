@echo off
setlocal
cd /d "%~dp0"
if exist "runtime\python.exe" (
  set "LAB_PYTHON=runtime\python.exe"
) else if exist ".venv\Scripts\python.exe" (
  set "LAB_PYTHON=.venv\Scripts\python.exe"
) else (
  echo Python libraries are not included in this source download.
  echo Run SETUP.bat once with Internet access, or use the complete OFFLINE ZIP.
  echo Extract the entire ZIP before starting; do not run from inside a ZIP viewer.
  pause
  exit /b 1
)
echo Starting Cloud Operations Lab. Keep this window open while using the app.
"%LAB_PYTHON%" launch.py %*
if errorlevel 1 (
  echo Startup failed. Read the message above and data\startup-error.log.
  pause
)
