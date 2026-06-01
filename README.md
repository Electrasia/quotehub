# QuoteHub

AI-powered quotation document processing system. Upload PDF quotations, extract structured data using AI, and search across all processed documents.

**Version:** v0.020.0 (release) / v0.021.0 (dev) — the running version is shown under the "QuoteHub" header in the app.

## Features

- **AI-Powered Extraction** — Upload PDF quotations and extract supplier, items, prices, dates using a local VLM (Vision Language Model)
- **Multi-Page Processing** — Automatically processes all pages with streaming progress feedback
- **Search & Filter** — Full-text search with prefix matching across suppliers, items, descriptions
- **Editable Results** — Review, edit, find & replace before saving
- **Backup/Restore** — Export/import quotations and PDFs as ZIP archives
- **Sortable Columns** — Click any column header to sort ascending/descending
- **PDF Viewer** — Double-click any item to view the original document
- **Duplicate Detection** — Warns when uploading a file that already exists
- **Configurable Popups** — Adjust success popup duration in settings
- **Version + Commit Display** — App header shows current version and commit hash for traceability

## Prerequisites

- **Docker** installed on your PC ([Install Docker](https://docs.docker.com/get-docker/))
- **AI Server** running with a Vision Language Model (VLM) — e.g., [LM Studio](https://lmstudio.ai/) with any OpenAI-compatible VLM

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Electrasia/quotehub.git
cd quotehub
git checkout main
```

### 2. First-time setup: create your config.json

The repo's `config.json` has empty `ai_endpoint` and `model` fields by design — these are your personal inputs. `deploy.sh` will create one from the template if missing, or copy manually:

```bash
cp config.example.json config.json
# Then edit config.json with your AI server details, OR fill them in via the app's Settings
```

### 3. Build and run

**Easiest** — use the deploy script (handles config, build, restart all in one):
```bash
./deploy.sh
```

**Or manually:**
```bash
docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) -t quodb .
docker run -d \
  --name quodb \
  --restart unless-stopped \
  -p 8000:8000 \
  -v $(pwd)/config.json:/app/config.json \
  -v quodb_data:/app/data \
  quodb
```

### 4. Open QuoteHub

Open your browser and go to **http://localhost:8000**

### 5. Connect to AI Server

1. Click **⚙ Preferences** → **Server Connection**
2. Enter your AI endpoint URL and model name
3. Click **Save Settings**
4. Click **Connect to AI Server** in the header

## Updating the Deployed App

When new code is pushed to `main` on GitHub, update the deployed PC with one command:

```bash
cd quotehub
./deploy.sh
```

This will:
1. Pull the latest from `main`
2. Bake the current commit hash into the new image
3. Stop and remove the old container
4. Start the new container with the same persistent mounts (no data loss)

## Versioning

- `VERSION` file in the repo root defines the current release (e.g. `0.021.0`)
- The commit hash is baked into the image at build time via the `GIT_COMMIT` Docker build arg
- The app header displays both: `v0.021.0 (a1b2c3d)`
- Versioning follows [Semantic Versioning](https://semver.org/):
  - `MAJOR` — breaking changes
  - `MINOR` — new features (backwards compatible)
  - `PATCH` — bug fixes

## Configuration

The `config.json` file stores your settings and is mounted as a Docker volume so it persists across rebuilds.

| Setting | Description | Default |
|---------|-------------|---------|
| `ai_endpoint` | AI server URL | `""` (user input) |
| `model` | Model name | `""` (user input) |
| `timeout` | Request timeout (seconds) | `90` |
| `max_retries` | Max retry attempts | `2` |
| `external_url` | QuoteHub URL for image access | `""` (auto localhost) |
| `popup_duration` | Success popup duration (seconds) | `3` |

**Note:** `ai_endpoint` and `model` are intentionally empty in the committed `config.json` and `config.example.json`. Configure them in Settings → Server Connection (recommended) or edit your local `config.json` directly.

## Project Structure

```
quotehub/
├── backend/
│   ├── main.py              # FastAPI application
│   └── requirements.txt     # Python dependencies
├── frontend/
│   └── index.html           # Single-page frontend
├── data/                    # Runtime data — NOT in the repo (gitignored). Created at
│   # build time and persists in the `quodb_data` Docker volume at runtime
│   ├── quotations.db        # SQLite database
│   ├── archive/             # Archived PDFs
│   └── temp/                # Temporary uploads
├── config.json              # Your personal settings (mounted, not in image)
├── config.example.json      # Template for config.json
├── deploy.sh                # One-command deploy/update script
├── VERSION                  # Current release version
├── Dockerfile               # Docker image definition
├── docker-compose.yml       # Docker Compose config
└── README.md                # This file
```

## Docker Commands

### Start
```bash
docker start quodb
```

### Stop
```bash
docker stop quodb
```

### View Logs
```bash
docker logs quodb
```

### Update (one command)
```bash
./deploy.sh
```

### Using Docker Compose
```bash
docker-compose up -d
```

## Data Persistence

- **config.json** — Mounted from host, survives rebuilds
- **data/** — Stored in Docker named volume `quodb_data`, survives container rebuilds
- **Database** — SQLite with FTS5 full-text search index
- **PDFs** — Archived in `data/archive/`

## Backup & Restore

1. Click **⚙ Preferences** → **Backup / Restore**
2. **Export**: Downloads a ZIP file with all quotations and PDFs
3. **Import**: Upload a ZIP or JSON file to restore data

## Troubleshooting

### AI Connection Fails
- Verify the AI server is running and accessible
- Check the endpoint URL in Settings → Server Connection
- Ensure the model is loaded in your AI server

### Search Returns No Results
- Check that quotations have been saved (confirmed, not just processed)
- Try partial terms (e.g., "amp" instead of "amplifier")

### Container Won't Start
- Check Docker logs: `docker logs quodb`
- Ensure port 8000 is not in use: `lsof -i :8000`
- Verify `config.json` exists in the project directory

## Tech Stack

- **Backend:** Python, FastAPI, SQLite (FTS5), httpx
- **Frontend:** Vanilla HTML/CSS/JavaScript (no frameworks)
- **AI:** Any OpenAI-compatible VLM API (LM Studio, vLLM, etc.)
- **Container:** Docker with Python 3.11-slim

## License

MIT
