#!/bin/bash
# Add Mise Python to PATH
export PATH="/mise/installs/python/3.13.13/bin:$PATH"

echo "Starting Miro Bot Overhaul Deployment..."

cd discord-bot

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    /mise/installs/python/3.13.13/bin/python -m venv venv
    if [ $? -ne 0 ]; then
        echo "❌ ERROR: Failed to create virtual environment."
        exit 1
    fi
fi

# Activate virtual environment
if [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "❌ ERROR: Could not find activation script."
    exit 1
fi

echo "Installing dependencies..."
/mise/installs/python/3.13.13/bin/pip install --upgrade pip
/mise/installs/python/3.13.13/bin/pip install -r requirements.txt

echo "Launching Miro Bot..."
/mise/installs/python/3.13.13/bin/python bot.py
