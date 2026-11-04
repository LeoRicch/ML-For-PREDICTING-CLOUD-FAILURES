@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto failed
if exist requirements-lock.txt (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir --progress-bar off -r requirements-lock.txt
) else (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir --progress-bar off -r requirements.txt
)
if errorlevel 1 goto failed
echo Setup complete. Use START_APP.bat to open the laboratory.
pause
exit /b 0
:failed
echo Setup failed. Check that Python 3.14 is installed and Internet access is available.
pause
exit /b 1
