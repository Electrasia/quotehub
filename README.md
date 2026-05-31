# QuoteHub

AI-powered quotation document processing system. Upload PDF quotations, extract structured data using AI, and search across all processed documents.

**Version:** v0.020.0

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

## Prerequisites

- **Docker** installed on your PC ([Install Docker](https://docs.docker.com/get-docker/))
- **AI Server** running with a Vision Language Model (VLM) — e.g., [LM Studio](https://lmstudio.ai/) with a model like `qwen/qwen3-vl-4b`

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/electrasia/quotehub.git
cd quotehub
```

### 2. Build the Docker image

```bash
docker build -t quodb .
```

### 3. Run the container

```bash
docker run -d \
  --name quodb \
  -p 8000:8000 \
  -v $(pwd)/config.json:/app/config.json \
  -v quodb_data:/app/data \
  quodb
```

### 4. Open QuoteHub

Open your browser and go to **http://localhost:8000**

### 5. Connect to AI Server

1. Click **⚙ Preferences** → **Server Connection**
2. Enter your AI endpoint URL (e.g., `http://ai_ip:port/v1/chat/completions`)
3. Enter your model name (e.g., `llm_name`)
4. Click **Save Settings**
5. Click **Connect to AI Server** in the header

## Configuration

The `config.json` file stores your settings and is mounted as a Docker volume so it persists across rebuilds.

| Setting | Description | Default |
|---------|-------------|---------|
| `ai_endpoint` | AI server URL | `http://localhost:1234/v1/chat/completions` |
| `model` | Model name | `qwen/qwen3-vl-4b` |
| `timeout` | Request timeout (seconds) | `90` |
| `max_retries` | Max retry attempts | `2` |
| `external_url` | QuoDB URL for image access | `http://localhost:8000` |
| `popup_duration` | Success popup duration (seconds) | `3` |

## Project Structure

```
quotehub/
├── backend/
│   ├── main.py              # FastAPI application
│   └── requirements.txt     # Python dependencies
├── frontend/
│   └── index.html           # Single-page frontend
├── data/                    # Runtime data (Docker volume)
│   ├── quotations.db        # SQLite database
│   ├── archive/             # Archived PDFs
│   └── temp/                # Temporary uploads
├── config.json              # Application settings
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

### Rebuild (after code changes)
```bash
docker stop quodb && docker rm quodb
docker build -t quodb .
docker run -d \
  --name quodb \
  -p 8000:8000 \
  -v $(pwd)/config.json:/app/config.json \
  -v quodb_data:/app/data \
  quodb
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

## Tech Stack

- **Backend:** Python, FastAPI, SQLite (FTS5), httpx
- **Frontend:** Vanilla HTML/CSS/JavaScript (no frameworks)
- **AI:** Any OpenAI-compatible VLM API (LM Studio, vLLM, etc.)
- **Container:** Docker with Python 3.11-slim

## License

MIT
