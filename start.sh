#!/bin/bash
set -e

echo "Starting Miro Bot Overhaul Deployment..."

# --- Auto-detect a usable Python 3 interpreter ---
PYTHON=""
for candidate in \
    "$(command -v python3 2>/dev/null)" \
    "$(command -v python 2>/dev/null)" \
    "/usr/local/bin/python3" \
    "/usr/bin/python3" \
    "/usr/bin/python"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYTHON="$candidate"
        break
    fi
done

# Fallback: probe any mise-installed python (whatever version is present)
if [ -z "$PYTHON" ] && [ -d "/mise/installs/python" ]; then
    PYTHON="$(ls -1 /mise/installs/python/*/bin/python 2>/dev/null | head -n1)"
fi

if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    echo "❌ ERROR: No Python 3 interpreter found on PATH or in /mise/installs/python."
    echo "   Tried: python3, python, /usr/local/bin/python3, /usr/bin/python3, mise installs."
    exit 1
fi

echo "✅ Using Python: $PYTHON ($($PYTHON --version 2>&1))"

cd discord-bot

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv venv
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

# Resolve the venv's python/pip explicitly so we don't accidentally use the system one
if [ -x "venv/bin/python" ]; then
    VENV_PY="venv/bin/python"
elif [ -x "venv/Scripts/python.exe" ]; then
    VENV_PY="venv/Scripts/python.exe"
else
    VENV_PY="$PYTHON"
fi

echo "Installing dependencies..."
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r requirements.txt

echo "Launching Miro Bot..."
exec "$VENV_PY" bot.py
