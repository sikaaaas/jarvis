@echo off
rem Jarvis - start script (Windows). Double-click me.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from https://www.python.org/downloads/ and tick "Add Python to PATH".
  pause
  exit /b 1
)
python setup.py
python main.py
pause
