@echo off
setlocal enabledelayedexpansion
title Miro Bot Overhaul - Startup
echo Starting Miro Bot Overhaul Deployment...

:: Detect Python
set PYTHON_CMD=
where python3 >nul 2>nul
if !errorlevel! equ 0 (
    set PYTHON_CMD=python3
) else (
    where python >nul 2>nul
    if !errorlevel! equ 0 (
        set PYTHON_CMD=python
    ) else (
        where py >nul 2>nul
        if !errorlevel! equ 0 (
            set PYTHON_CMD=py
        )
    )
)

if "%PYTHON_CMD%"=="" (
    echo ❌ ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.8+ to continue.
    pause
    exit /b 1
)

echo Using: %PYTHON_CMD%
cd discord-bot

if not exist venv (
    echo Creating virtual environment...
    %PYTHON_CMD% -m venv venv
    if !errorlevel! neq 0 (
        echo ❌ ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate
if !errorlevel! neq 0 (
    echo ❌ ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)

echo Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo Launching Miro Bot...
python bot.py
pause
