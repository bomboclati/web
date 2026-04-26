@echo off
setlocal enabledelayedexpansion
title Miro Bot Overhaul - Startup
echo Starting Miro Bot Overhaul Deployment...

:: Set Mise Python path
set "PYTHON_DIR=C:\mise\installs\python\3.13.13"
set "PATH=%PYTHON_DIR%\bin;%PATH%"

cd discord-bot

if not exist venv (
    echo Creating virtual environment...
    %PYTHON_DIR%\bin\python.exe -m venv venv
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
%PYTHON_DIR%\bin\pip.exe install --upgrade pip
%PYTHON_DIR%\bin\pip.exe install -r requirements.txt

echo Launching Miro Bot...
%PYTHON_DIR%\bin\python.exe bot.py
pause
