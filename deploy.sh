#!/bin/bash
# QuoteHub deploy script
# Run this from inside the cloned quotehub repository on the deployed PC.
# Pulls the latest from the branch you're currently on (main for production,
# dev for testing), rebuilds the image with the current commit hash baked in,
# and restarts the container. All data (config.json + database) persists via mounts.

set -e

echo ""
echo "================================================"
echo "  QuoteHub deploy"
echo "================================================"
echo ""

# Check we're in a git repo
if [ ! -d .git ]; then
    echo "ERROR: This script must be run from inside the quotehub repository."
    exit 1
fi

# Check for config.json
if [ ! -f config.json ]; then
    if [ -f config.example.json ]; then
        echo ">> config.json not found. Copying from config.example.json..."
        cp config.example.json config.json
        echo ">> config.json created. Please edit it with your AI server details,"
        echo "   or use Settings -> Server Connection in the app."
        echo ""
    else
        echo "ERROR: config.json not found and no config.example.json to copy from."
        exit 1
    fi
fi

# Pull latest from the current branch (best-effort; skip if local is ahead of origin)
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo ">> Pulling latest from origin/$CURRENT_BRANCH..."
if ! git pull --ff-only origin "$CURRENT_BRANCH" 2>/dev/null; then
    echo "   (local is ahead of origin/$CURRENT_BRANCH, skipping pull — building local HEAD)"
fi

# Get the current commit hash (local, no network needed)
GIT_COMMIT=$(git rev-parse --short HEAD)
export GIT_COMMIT
echo ">> Current commit: $GIT_COMMIT"

# Build and start using docker compose (handles image build, container
# replacement, healthcheck, and volume mounts from docker-compose.yml)
echo ">> Building and starting via docker compose..."
docker compose up -d --build

# Show the running version
echo ""
echo "================================================"
echo "  Deploy complete!"
echo "================================================"
echo ""
echo "Running version:"
docker exec quodb cat /app/VERSION 2>/dev/null || echo "(could not read version)"
docker exec quodb cat /app/GIT_COMMIT 2>/dev/null || echo "(could not read commit)"
echo ""
echo "App is available at: http://localhost:8000"

# Show initial master password (only on fresh install or after password reset)
# Wait a moment for the container to finish initializing
sleep 2
INIT_PW=$(docker exec quodb cat /app/data/init_password.txt 2>/dev/null || true)
if [ -n "$INIT_PW" ]; then
    echo ""
    echo "================================================"
    echo "  INITIAL MASTER PASSWORD"
    echo "================================================"
    echo "  Username: master"
    echo "  Password: $INIT_PW"
    echo ""
    echo "  Login and change this password immediately."
    echo "================================================"
fi
echo ""
