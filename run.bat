@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title RedemManager Launcher
echo ==========================================
echo RedemManager - LDPlayer 9 Dashboard
echo ==========================================
echo.

set "PROJECT_DIR=%~dp0"
set "VENV_DIR=%PROJECT_DIR%.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "UVICORN_EXE=%VENV_DIR%\Scripts\uvicorn.exe"
set "REQUIREMENTS=%PROJECT_DIR%requirements.txt"
set "ENV_FILE=%PROJECT_DIR%.env"

cd /d "%PROJECT_DIR%"

echo [1/5] Checking Python installation...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found in PATH
    echo Please install Python 3.11+ from https://python.org
    pause
    exit /b 1
)
python --version

echo.
echo [2/5] Checking virtual environment...
if not exist "%VENV_DIR%" (
    echo Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
) else (
    echo Virtual environment exists.
)

echo.
echo [3/5] Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)

echo.
echo [4/5] Installing/updating dependencies...
"%PYTHON_EXE%" -m pip install --upgrade pip >nul 2>&1
"%PYTHON_EXE%" -m pip install -r "%REQUIREMENTS%"
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [5/5] Checking configuration...
if not exist "%ENV_FILE%" (
    echo WARNING: .env file not found, copying from .env.example
    copy "%PROJECT_DIR%.env.example" "%ENV_FILE%" >nul
)

echo.
echo Validating paths...
"%PYTHON_EXE%" -c "import sys; sys.path.insert(0, r'%PROJECT_DIR%..'); from config import validate_paths; errors = validate_paths(); [print('ERROR:', e) for e in errors]; sys.exit(1 if errors else 0); print('All paths valid.')"
if %errorlevel% neq 0 (
    echo.
    echo Configuration errors found. Please edit .env and fix paths.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo Starting RedemManager...
echo Dashboard will be available at:
echo   http://127.0.0.1:8765
echo ==========================================
echo.
echo Press Ctrl+C to stop the server.
echo.

"%PYTHON_EXE%" -c "import uvicorn; from app import app; uvicorn.run(app, host='127.0.0.1', port=8765, log_level='info')"

echo.
echo Server stopped.
pause