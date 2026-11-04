@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" train.py --runs-per-load 200
if errorlevel 1 (
  echo Model preparation failed. Read the error above.
) else (
  echo Model ready. New experiments will use it.
)
pause
