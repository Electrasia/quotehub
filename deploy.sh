#!/bin/bash
# QuoteHub deploy script
# Supports fresh install and update. Auto-detects mode via Docker volume.
# Usage: ./deploy.sh [--force]
#   --force: skip "already up to date" check, rebuild anyway
set -euo pipefail

FORCE=false
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
    esac
done

# ─── Functions ────────────────────────────────────────────────────────────────

wait_for_health() {
    local timeout=${1:-30}
    local url="http://localhost:8000/health"
    for i in $(seq 1 "$timeout"); do
        if curl -sf "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

check_container_running() {
    if docker ps --filter name=quodb --filter status=running --format "{{.Names}}" 2>/dev/null | grep -q quodb; then
        return 0
    else
        return 1
    fi
}

# ─── Header ──────────────────────────────────────────────────────────────────

echo ""
echo "================================================"
echo "  QuoteHub deploy"
echo "================================================"
echo ""

# ─── Preflight (both modes) ──────────────────────────────────────────────────

if [ ! -d .git ]; then
    echo "ERROR: This script must be run from inside the quotehub repository."
    exit 1
fi

if [ ! -f config.json ]; then
    if [ -f config.example.json ]; then
        echo ">> config.json not found. Copying from config.example.json..."
        cp config.example.json config.json
        echo ">> config.json created. Please edit it with your AI server details."
    else
        echo "ERROR: config.json not found and no config.example.json to copy from."
        exit 1
    fi
fi

GIT_COMMIT=$(git rev-parse --short HEAD)
export GIT_COMMIT

# ─── Mode detection ──────────────────────────────────────────────────────────

if docker volume inspect quodb_data >/dev/null 2>&1; then
    MODE="update"
else
    MODE="install"
fi

# ─── Fresh install ───────────────────────────────────────────────────────────

if [ "$MODE" = "install" ]; then
    echo "=== Fresh install ==="
    echo ""

    docker compose up -d --build

    echo ">> Waiting for health check..."
    if ! wait_for_health 30; then
        echo "ERROR: Container failed to become healthy within 30s."
        docker compose logs quodb --tail 30
        exit 1
    fi

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
    exit 0
fi

# ─── Update ──────────────────────────────────────────────────────────────────

echo "=== Existing install detected — running update ==="
echo ""

# D1. Current version
OLD_VERSION=$(cat VERSION 2>/dev/null || echo "unknown")
echo ">> Current version: $OLD_VERSION"

# Verify upstream branch exists
if ! git rev-parse --abbrev-ref @{u} >/dev/null 2>&1; then
    echo "ERROR: No upstream branch set."
    echo "Run: git branch --set-upstream-to=origin/<branch>"
    exit 1
fi

# D3. Fetch
echo ">> Fetching from origin..."
git fetch origin

# D4. Incoming changes
INCOMING=$(git log HEAD..@{u} --oneline 2>/dev/null || true)
if [ -z "$INCOMING" ] && [ "$FORCE" = false ]; then
    echo ">> Already up to date."
    exit 0
fi

if [ -n "$INCOMING" ]; then
    echo ">> Incoming changes:"
    echo "$INCOMING"
    echo ""
fi

# D5. Pre-update backup
echo ">> Ensuring container is running for backup..."
if ! check_container_running; then
    echo "   Container not running — starting it..."
    docker compose up -d
    echo ">> Waiting for health check..."
    if ! wait_for_health 30; then
        echo "ERROR: Cannot start container for backup — aborting update."
        exit 1
    fi
fi

echo ">> Running pre-update backup..."
if ! docker exec quodb python -m backend.cli backup pre-update --version "$OLD_VERSION"; then
    echo "ERROR: Pre-update backup failed — aborting update for safety."
    exit 1
fi
echo ""

# D6. Pull
echo ">> Pulling from origin..."
git pull --ff-only

# D7. New version
NEW_VERSION=$(cat VERSION 2>/dev/null || echo "unknown")
echo ">> Updating: $OLD_VERSION → $NEW_VERSION"
echo ""

# D9. Rebuild
echo ">> Rebuilding container..."
docker compose up -d --build --force-recreate

# D10. Health check
echo ">> Waiting for health check..."
if ! wait_for_health 30; then
    echo "ERROR: Container failed to become healthy within 30s."
    echo "Update may have broken — see logs below:"
    docker compose logs quodb --tail 30
    exit 1
fi

# D11-D12. Success
echo ""
echo "================================================"
echo "  Update complete!"
echo "================================================"
echo ""
echo "Running version:"
docker exec quodb cat /app/VERSION 2>/dev/null || echo "(could not read version)"
docker exec quodb cat /app/GIT_COMMIT 2>/dev/null || echo "(could not read commit)"
echo ""
echo "App is available at: http://localhost:8000"
echo ""
echo ">> Recent logs:"
docker compose logs quodb --tail 20
echo ""
