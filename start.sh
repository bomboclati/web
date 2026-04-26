#!/bin/bash
echo "Starting Miro Bot Overhaul Deployment..."
cd discord-bot
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python -m venv venv
fi
source venv/Scripts/activate || source venv/bin/activate
echo "Installing dependencies..."
pip install -r requirements.txt
echo "Launching Miro Bot..."
python bot.py
