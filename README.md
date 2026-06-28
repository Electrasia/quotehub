# QuoteHub

Quotation document processing system. Upload PDF or XLSX quotations, extract structured data, and search across all processed documents.

**Version:** v0.065.0 — shown in the app header.

## Features

- AI-powered extraction for scanned PDFs, text PDFs, and XLSX files
- Multi-page processing with streaming progress
- Full-text search with prefix matching
- Editable results with find-and-replace
- Encrypted backup/restore (AES-256-GCM `.quodb` packages)
- Supplier database — contacts, aliases, brands, scan, merge, purge
- File-at-rest encryption (AES-256-GCM, optional)
- Auto-backup with daily/weekly retention
- Three-role authentication (user / admin / master)
- CLI for pre-update backups and key management

## Prerequisites

- **Docker** — [Install Docker](https://docs.docker.com/get-docker/)
- **AI Server** — any OpenAI-compatible VLM API ([LM Studio](https://lmstudio.ai/), vLLM, etc.)

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/Electrasia/quotehub.git
cd quotehub
cp config.example.json config.json
# Edit config.json with your AI server details, or fill them in via Settings
```

### 2. Build and run

```bash
export FILE_ENCRYPTION_KEY="$(python3 -c 'import os;print(os.urandom(32).hex())')"  # optional
./deploy.sh
```

### 3. Open QuoteHub

Open **http://localhost:8000** in your browser.

### 4. Connect AI Server

Go to **Settings** → enter your AI endpoint and model → **Save Settings** → **Connect to AI Server** in the header.

## Updating

```bash
./deploy.sh
```

Auto-detects fresh install vs update. Creates a pre-update backup automatically when updating.

## Authentication

| Role | Search | Upload / Process / Edit | AI Settings | Export / Import | User Mgmt |
|------|--------|------------------------|-------------|----------------|-----------|
| **user** | ✓ | — | — | — | — |
| **admin** | ✓ | ✓ | view only | — | — |
| **master** | ✓ | ✓ | ✓ | ✓ | ✓ |

On first startup, a `master` user is created with a random password printed to the logs. Change it immediately.

## Configuration

`config.json` is mounted from the host and persists across rebuilds.

| Setting | Description | Default |
|---------|-------------|---------|
| `ai_endpoint` | AI server URL | `""` |
| `model` | Model name | `""` |
| `timeout` | Request timeout (seconds) | `90` |
| `max_retries` | Max retry attempts | `2` |
| `external_url` | QuoteHub URL for image access | `""` |
| `popup_duration` | Success popup duration (seconds) | `3` |
| `ocr_enabled` | Enable OCR for scanned PDFs | `true` |
| `extraction_enabled` | Enable AI extraction | `true` |
| `max_upload_size_mb` | Max upload file size (1–20 MB) | `5` |
| `trust_proxy_headers` | Trust proxy headers (NPM) | `false` |

## Backup & Restore

All exports use AES-256-GCM encrypted `.quodb` packages. Export/import are **master-only**.

- **Manual**: Settings → Backup / Restore → Export / Import
- **Automatic**: Daily at 03:00, weekly promotion on Sundays, event-based before imports and updates

## Project Structure

```
quotehub/
├── backend/              Python, FastAPI, SQLite
├── frontend/             Vanilla HTML/CSS/JS
├── data/                 Runtime data (Docker volume)
├── config.json           Personal settings (mounted)
├── deploy.sh             One-command deploy/update
├── VERSION               Current release version
├── Dockerfile            Docker image
└── docker-compose.yml    Docker Compose config
```

## Tech Stack

- **Backend:** Python, FastAPI, SQLite (FTS5), httpx
- **Frontend:** Vanilla HTML/CSS/JavaScript (no frameworks)
- **AI:** Any OpenAI-compatible VLM API
- **Container:** Docker with Python 3.11-slim, non-root user
- **Encryption:** AES-256-GCM at rest + for backup packages

## Troubleshooting

- Container not healthy: `docker compose logs quodb --tail 50`
- Reset and redeploy: `docker compose down && ./deploy.sh`
- Permission errors: ensure Docker has access to the data volume
- Health check: `curl http://localhost:8000/health`

## License

MIT
