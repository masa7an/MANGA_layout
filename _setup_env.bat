@echo off
REM ---------------------------------------------------------------
REM MANGA_layout - virtual environment setup
REM
REM Creates venv/ with Python 3.12 and installs requirements.txt.
REM Run this once after cloning the repository on a new PC.
REM
REM Safe to run again: an existing venv is kept and only the
REM packages are re-checked.
REM ---------------------------------------------------------------

setlocal
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    echo [1/2] venv already exists - skipping creation.
    goto install
)

echo [1/2] Creating venv with Python 3.12 ...
py -3.12 -m venv venv
if errorlevel 1 goto no_python

:install
echo.
echo [2/2] Installing packages from requirements.txt ...
"venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
"venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto pip_failed

echo.
echo ================================================
echo  Setup complete.
echo ================================================
"venv\Scripts\python.exe" -c "import sys, PySide6; print('Python  ', sys.version.split()[0]); print('PySide6 ', PySide6.__version__)"
echo.
echo Run scripts with: venv\Scripts\python.exe main.py
echo.
pause
exit /b 0

:no_python
echo.
echo ERROR: could not create the venv. Python 3.12 may be missing.
echo        Check the installed versions with:  py --list
echo        Get it from https://www.python.org/downloads/
echo.
pause
exit /b 1

:pip_failed
echo.
echo ERROR: package installation failed.
echo        Check your network connection and try again.
echo.
pause
exit /b 1
