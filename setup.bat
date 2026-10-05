@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :venv_error
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :activate_error

if exist "requirements.txt" (
    echo Installing dependencies from requirements.txt...
    python -m pip install -r requirements.txt
) else (
    echo requirements.txt not found. Installing Flet...
    python -m pip install flet
)
if errorlevel 1 goto :install_error

echo Starting the application...
python main.py
if errorlevel 1 goto :app_error

echo The application has closed.
pause
exit /b 0

:venv_error
echo Failed to create .venv. Check that Python is installed and available on PATH.
pause
exit /b 1

:activate_error
echo Failed to activate .venv.
pause
exit /b 1

:install_error
echo Failed to install dependencies.
pause
exit /b 1

:app_error
echo The application exited with an error.
pause
exit /b 1
