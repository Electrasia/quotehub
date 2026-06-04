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

# Build the image with commit hash baked in
echo ">> Building Docker image..."
docker build --build-arg GIT_COMMIT=$GIT_COMMIT -t quodb .

# Stop and remove the old container (if it exists)
echo ">> Stopping old container (if any)..."
docker stop quodb 2>/dev/null || true
docker rm quodb 2>/dev/null || true

# Start the new container with persistent mounts
echo ">> Starting new container..."
docker run -d \
    --name quodb \
    --restart unless-stopped \
    -p 8000:8000 \
    -v $(pwd)/config.json:/app/config.json \
    -v quodb_data:/app/data \
    quodb

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
echo ""
