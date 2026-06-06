#!/bin/bash
# dev.sh — Local development server for QuoteHub (no Docker)
# Usage: bash dev.sh
#
# What it does:
#  1. Creates .venv/ if missing
#  2. Installs backend/requirements.txt into the venv
#  3. Ensures data/ subdirs exist (data/temp, data/archive, data/images)
#  4. Starts uvicorn with --reload so backend code changes are picked up
#     automatically (frontend changes need a browser refresh).
#
# ════════════════════════════════════════════════════════════════════
# ⚠  IMPORTANT: DEV MODE USES A SEPARATE DATABASE
# ════════════════════════════════════════════════════════════════════
# Dev mode reads/writes ./data/ directly. This is INDEPENDENT of the
# Docker volume "quodb_data" used by deploy.sh.
#
# Consequences:
#   • The master password in dev is DIFFERENT from the Docker deploy.
#   • Uploads / saved quotations in dev are NOT visible in the Docker app.
#   • First-time dev login uses the bootstrap password (see below).
#
# To reset the dev master password to defaults:
#     rm data/init_password.txt data/quotations.db
#   then run `bash dev.sh` again — a new password will be generated
#   and shown in the terminal.
#
# To copy data FROM the Docker deployment INTO dev:
#     docker stop quodb
#     sudo cp -r /var/lib/docker/volumes/quodb_data/_data/* ./data/
#     bash dev.sh
#
# To copy data FROM dev BACK TO the Docker deployment:
#     docker stop quodb
#     sudo cp -r ./data/* /var/lib/docker/volumes/quodb_data/_data/
#     bash deploy.sh
# ════════════════════════════════════════════════════════════════════

set -e

# Switch to project root (where this script lives)
cd "$(dirname "$0")"

echo ""
echo "================================================"
echo "  QuoteHub dev server (no Docker)"
echo "================================================"
echo ""

# Create venv if missing
if [ ! -d ".venv" ]; then
    echo ">> Creating .venv..."
    python3 -m venv .venv
fi

# Install / update requirements
echo ">> Installing dependencies into .venv..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r backend/requirements.txt
echo "   done."

# Ensure data dirs exist (mirror Dockerfile layout)
mkdir -p data/temp data/archive data/images

# Confirm config.json exists
if [ ! -f config.json ]; then
    if [ -f config.example.json ]; then
        echo ">> config.json not found. Copying from config.example.json..."
        cp config.example.json config.json
        echo "   Edit config.json with your AI server details, or use Settings in the app."
    else
        echo "ERROR: config.json not found and no config.example.json to copy from."
        exit 1
    fi
fi

# Show dev mode credentials if init password file exists
if [ -f data/init_password.txt ]; then
    INIT_PW=$(cat data/init_password.txt)
    echo ""
    echo "┌──────────────────────────────────────────────────────────────┐"
    echo "│  DEV MODE — First-time master login credentials              │"
    echo "│                                                              │"
    echo "│    Username: master                                          │"
    echo "│    Password: $INIT_PW"
    echo "│                                                              │"
    echo "│  You'll be asked to change this on first login.              │"
    echo "│  (This is separate from your Docker deploy credentials.)     │"
    echo "└──────────────────────────────────────────────────────────────┘"
    echo ""
elif [ -f data/quotations.db ]; then
    echo ">> Local DB exists (no init_password.txt — master password already changed)."
    echo "   Use your dev master password to log in."
    echo ""
else
    echo ">> No local DB found. A new master password will be generated on first run."
    echo ""
fi

echo ">> Starting uvicorn on http://localhost:8000 (auto-reload enabled)..."
echo "   Press Ctrl+C to stop."
echo ""

exec .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
