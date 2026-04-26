@echo off
title Miro Bot Overhaul - Startup
echo Starting Miro Bot Overhaul Deployment...
cd discord-bot
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate
echo Installing dependencies...
pip install -r requirements.txt
echo Launching Miro Bot...
python bot.py
pause
