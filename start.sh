#!/bin/bash
echo "Starting Miro Bot Overhaul Deployment..."

# Function to find the python command
find_python() {
    if command -v python3 &>/dev/null; then
        echo "python3"
    elif command -v python &>/dev/null; then
        echo "python"
    elif command -v py &>/dev/null; then
        echo "py"
    else
        echo ""
    fi
}

PYTHON_CMD=$(find_python)

if [ -z "$PYTHON_CMD" ]; then
    echo "❌ ERROR: Python is not installed or not in PATH."
    echo "Please install Python 3.8+ to continue."
    exit 1
fi

echo "Using: $($PYTHON_CMD --version)"

cd discord-bot

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv venv
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
pip install --upgrade pip
pip install -r requirements.txt

echo "Launching Miro Bot..."
python bot.py
