@echo off
REM ======================================================================
REM  RedemManager - Export Source Package
REM  Creates a clean zip without venv, cache, databases, logs
REM ======================================================================

set PROJECT=RedemManager
set OUTDIR=D:\AccMicro\TOOL\VIP\export
set ZIPNAME=%PROJECT%_source_%date:~-4,4%%date:~-10,2%%date:~-7,2%.zip
set SRCDIR=%~dp0

mkdir "%OUTDIR%" 2>nul

echo.
echo [1/4] Cleaning temp files...
cd /d "%SRCDIR%"
del /s /q /f __pycache__ 2>nul
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
del /s /q /f *.pyc 2>nul
del /s /q /f *.pyo 2>nul
del /s /q /f .coverage 2>nul
rd /s /q .pytest_cache 2>nul
rd /s /q .venv 2>nul

echo.
echo [2/4] Removing runtime data (databases, logs, local env)...
del /q data\*.db data\*-shm data\*-wal 2>nul
del /q logs\*.log 2>nul
del /q .env 2>nul

echo.
echo [3/4] Creating zip with PowerShell (no external tools needed)...
powershell -NoProfile -Command "$zip = '%OUTDIR%\%ZIPNAME%'; $src = '%SRCDIR%'; if (Test-Path $zip) { Remove-Item $zip -Force }; Add-Type -AssemblyName 'System.IO.Compression.FileSystem'; [IO.Compression.ZipFile]::CreateFromDirectory($src, $zip, [IO.Compression.CompressionLevel]::Optimal, $false); Write-Host 'Created:' $zip"

echo.
echo [4/4] Done.
echo.
echo ======================================================================
echo Package: %OUTDIR%\%ZIPNAME%
echo.
echo Contents included:
echo   - All .py source (app, config, services, tests, workflow, database, main)
echo   - Templates, static assets (CSS, JS, images)
echo   - requirements.txt, README.md, run.bat, .env.example
echo   - tests/ (full test suite)
echo.
echo Excluded:
echo   - .venv/ (virtual environment - recreate with: python -m venv .venv)
echo   - __pycache__/, .pytest_cache/
echo   - data/*.db, data/*-shm, data/*-wal (runtime databases)
echo   - logs/*.log
echo   - .env (contains local paths/secrets - use .env.example as template)
echo ======================================================================
echo.

pause