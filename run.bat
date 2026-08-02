@echo off
REM ---------------------------------------------------------------
REM MANGA_layout - launcher
REM
REM Starts the editor using the project's venv.
REM Optionally pass a project folder:
REM     run.bat samples\basic
REM
REM If the venv is missing, run _setup_env.bat first.
REM ---------------------------------------------------------------

setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" goto no_venv

"venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
exit /b 0

:no_venv
echo.
echo ERROR: venv not found.
echo        Run _setup_env.bat first.
echo.
pause
exit /b 1
