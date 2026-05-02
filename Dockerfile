# Pin a Python runtime so the deployment platform never falls back to a
# bare container without Python (which is what crashed start.sh after the
# old /mise/installs/python/3.13.13 path disappeared).
FROM python:3.13-slim

# System packages chromadb / cryptography sometimes need to build wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so they cache when only source changes.
COPY discord-bot/requirements.txt /app/discord-bot/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/discord-bot/requirements.txt

# Copy the rest of the project.
COPY . /app
RUN chmod +x /app/start.sh || true

# The data_manager writes to ./data so make sure the workdir is writable.
WORKDIR /app/discord-bot
RUN mkdir -p data && chmod 755 data

CMD ["python", "bot.py"]
