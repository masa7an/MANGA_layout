@echo off
REM ---------------------------------------------------------------
REM MANGA_layout - settings editor
REM
REM Opens a small window to edit data\settings.json.
REM See section 6.28 of the requirements document.
REM
REM If the venv is missing, run _setup_env.bat first.
REM ---------------------------------------------------------------

setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" goto no_venv

"venv\Scripts\python.exe" tools\settings_editor.py
if errorlevel 1 pause
exit /b 0

:no_venv
echo.
echo ERROR: venv not found.
echo        Run _setup_env.bat first.
echo.
pause
exit /b 1
