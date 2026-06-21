# QuoteHub

AI-powered quotation document processing system. Upload PDF or XLSX quotations, extract structured data using AI, and search across all processed documents.

**Version:** v0.063.0 — the running version is shown under the "QuoteHub" header in the app.

## Features

- **AI-Powered Extraction** — Upload PDF or XLSX quotations and extract supplier, items, prices, dates using a local VLM (Vision Language Model)
- **Multi-Page Processing** — Automatically processes all pages with streaming progress feedback
- **Search & Filter** — Full-text search with prefix matching across suppliers, items, descriptions
- **Editable Results** — Review, edit, find & replace before saving
- **Encrypted Backup/Restore** — Export/import quotations and PDFs as AES-256-GCM encrypted `.quodb` packages
- **Sortable Columns** — Click any column header to sort ascending/descending
- **PDF Viewer** — Double-click any item to view the original document
- **Duplicate Detection** — Warns when uploading a file that already exists
- **Review Re-entry** — Click any "✓ Ready to review" file in the queue to re-open the review screen
- **Smart Navigation** — Cancel/save routes to the file queue, not the upload page, when files remain
- **Configurable Popups** — Adjust success popup duration in settings
- **Version + Commit Display** — App header shows current version and commit hash for traceability
- **Auto-Backup** — Automatic daily encrypted backups at 03:00 with weekly promotion, event-based backups (pre-import, pre-update), and retention management
- **Internal Backup Key** — Machine-bound HKDF-wrapped AES-256-GCM key hierarchy, rotatable via CLI
- **CLI Interface** — Pre-update backups and key management via `python -m backend.cli`
- **File-at-Rest Encryption** — AES-256-GCM encrypted PDF/XLSX storage, transparent decrypt on read, keyed by `FILE_ENCRYPTION_KEY` env var
- **Non-Root Container** — Runs as `quodb` user (UID 1001) via `gosu` privilege drop; no root processes in production
- **Locked-Down API Docs** — `/docs`, `/redoc`, `/openapi.json` disabled by default; toggle with `QUODB_DOCS_ENABLED=true`
- **Host Header Protection** — `TrustedHostMiddleware` prevents host header injection attacks
- **AI Output Validation** — LLM and VLM responses validated against Pydantic models; VLM responses capped at 100 KB to prevent runaway memory usage
- **Upload Validation Pipeline** — Content-Length check → extension filter → path traversal guard → empty-stem check → size limit → magic bytes verification → encryption → write

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

### 2. First-time setup: create your config.json and encryption key

The repo's `config.json` has empty `ai_endpoint` and `model` fields by design — these are your personal inputs. `deploy.sh` will create one from the template if missing, or copy manually:

```bash
cp config.example.json config.json
# Then edit config.json with your AI server details, OR fill them in via the app's Settings
```

**Optional (recommended):** Generate a file-encryption key for at-rest encryption of uploaded PDFs/XLSX:

```bash
python3 -c "import os; print(os.urandom(32).hex())"
# Example output: 8f3a1c2b...
# Set this as FILE_ENCRYPTION_KEY in your environment before running deploy.sh
```

### 3. Build and run

**Easiest** — use the deploy script (handles config, build, restart all in one):
```bash
export FILE_ENCRYPTION_KEY="your-64-hex-char-key"  # omit if not using encryption
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
  -e FILE_ENCRYPTION_KEY="your-64-hex-char-key" \
  -e QUODB_DOCS_ENABLED=false \
  quodb
```

### 4. Open QuoteHub

Open your browser and go to **http://localhost:8000**

### 5. Connect to AI Server

1. Go to **Settings** (top nav bar) and enter your AI endpoint URL and model name, then click **Save Settings**
2. Click **Connect to AI Server** in the header

## Updating the Deployed App

When new code is pushed to `main` on GitHub, update the deployed PC with one command:

```bash
cd quotehub
./deploy.sh
```

Like the first-time setup, set `FILE_ENCRYPTION_KEY` in your environment for the new container to pick it up:

```bash
export FILE_ENCRYPTION_KEY="your-64-hex-char-key"
./deploy.sh
```

This will:
1. Pull the latest from `main`
2. Bake the current commit hash into the new image
3. Stop and remove the old container
4. Start the new container with the same persistent mounts (no data loss)

## Versioning

- `VERSION` file in the repo root defines the current release (e.g. `0.063.0`)
- The commit hash is baked into the image at build time via the `GIT_COMMIT` Docker build arg
- The app header displays both: `v0.063.0 (commit hash)`
- Versioning follows [Semantic Versioning](https://semver.org/):
  - `MAJOR` — breaking changes
  - `MINOR` — new features (backwards compatible)
  - `PATCH` — bug fixes

## Authentication

QuoteHub has a 3-role authentication system (introduced in `0.030.0`). All access — except the public pages (login, version, and PDF/image serving) — requires a session cookie.

### Roles

| Role | Search | Upload / Process / Edit / Delete | AI Settings | Export / Import | User Management | Auto-Backup Restore |
|------|--------|-----------------------------------|-------------|----------------|-----------------|---------------------|
| **user** | ✓ | — | — | — | — | — |
| **admin** | ✓ | ✓ | view only (fields disabled) | — | — | status only |
| **master** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

A short summary:

- **user** — read-only access. Can search and view PDFs. Cannot upload, edit, or change settings.
- **admin** — day-to-day operations. Can upload, process, edit, delete, view logs, view AI settings (read-only), and see auto-backup status. **Cannot:** change General settings, modify AI Settings, export/import backups, access System Cleanup, restore automatic backups, or manage users. Those are master-only.
- **master** — full control, including all settings, export/import, auto-backup restore, cleanup, and user management.

### First-Run Master Password

On the very first startup (or after the database is wiped), QuoteHub automatically creates a `master` user and generates a random 16-character password. The password is:

1. **Printed once to the container logs.** Run `docker logs quodb` (or `docker compose logs`) immediately after the first start and look for the `=== INITIAL MASTER PASSWORD ===` banner.
2. **Written to `data/init_password.txt` inside the Docker volume** with `chmod 600`. The file is auto-deleted the first time the master changes the password.

Log in as `master` with that password, then **immediately change the password** (you will be forced to).

### Where to Find the Initial Master Password (Recovery)

If you missed the password when it was first generated, recover it from either the file or the logs. Try them in this order — the file is the more reliable of the two.

**1. Read the file inside the container** (easiest, works while the container is running):

```bash
docker exec quodb cat /app/data/init_password.txt
```

If the file exists, it prints the password and you can log in. If the command says *"No such file or directory"*, the file was already auto-deleted (which means the master has already changed the password at some point) — skip to step 3.

**2. Check the container logs:**

```bash
docker logs quodb 2>&1 | grep -A2 "INITIAL MASTER PASSWORD"
```

This works only if the container has not been restarted since the first startup (the banner is in the startup logs, not in the access logs). If `grep` finds nothing, the logs have rolled past it — skip to step 3.

**3. If both the file and the logs are gone**, you have to follow the [Reset Master Password](#reset-master-password) steps below. This is destructive: it removes all user accounts (but keeps all quotations and PDFs intact), then the next startup generates a brand-new master password.

### Forced Password Change (`must_change_password`)

When a user is created (or when the master logs in for the first time) with a temporary password, the app blocks normal access and shows a **Change Password** form. The form requires:

- The current (temporary) password
- A new password (minimum 6 characters)
- A confirmation of the new password

The app only becomes usable after this change succeeds.

### Server-Connection (AI Settings) Lock for Admin

When an **admin** opens **Settings → Server Connection**, the AI fields (endpoint, model, external URL, timeout, max retries, popup duration) are visible but disabled. Hovering each field shows the tooltip *"Only Master can change AI settings"*. The **Save** button is also disabled. Even if the disabled state is bypassed via dev tools, the server enforces the same rule — `POST /config` requires the **master** role and returns `403` otherwise.

### Reset Master Password

If the master password is truly lost (file deleted, logs gone, no other master to ask), recover access by deleting all `users` rows in the SQLite database and restarting the container. The next startup will detect that no master exists and generate a new random password.

```bash
# 1. Stop the container
docker stop quodb

# 2. Remove all users (preserves quotations + PDFs)
docker run --rm -v quodb_data:/data alpine sh -c \
  'apk add --no-cache sqlite && sqlite3 /data/quotations.db "DELETE FROM users;"'

# 3. Start the container again
docker start quodb

# 4. Read the new password from the logs
docker logs quodb 2>&1 | grep -A1 "INITIAL MASTER PASSWORD"
```

> **What this does:** it only deletes the `users` table — no quotations or PDFs are touched. The new password is generated and printed exactly as on a first run; log in as `master` and change it immediately.
>
> **Side effect:** any `admin` and `user` accounts you had created are also deleted. You will have to re-create them from the Users Management panel after the new master is set up.

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
| `ocr_enabled` | Enable OCR for scanned PDFs | `true` |
| `ocr_fallback_to_llm` | Use vision LLM when OCR quality is low | `true` |
| `extraction_enabled` | Enable AI extraction (ON/OFF toggle) | `true` |
| `max_upload_size_mb` | Maximum upload file size (1–20 MB) | `5` |
| `trust_proxy_headers` | Trust proxy headers (Nginx Proxy Manager) | `false` |

### Extraction Pipeline

| File Type | Method |
|-----------|--------|
| Scanned PDF | Vision LLM (page-by-page image analysis, 200 DPI) |
| Text PDF | Text LLM (all pages combined, max_tokens: 4096) |
| XLSX | Text LLM (each sheet processed separately, max_tokens: 8192) |
| Any fail | Local rules fallback |
| AI disabled | Local rules only |

**Note:** `ai_endpoint` and `model` are intentionally empty in the committed `config.json` and `config.example.json`. Configure them in Settings → Server Connection (recommended) or edit your local `config.json` directly.

## Project Structure

```
quotehub/
├── backend/
│   ├── main.py              # FastAPI application, middleware, router registration
│   ├── utils.py             # Shared utilities (load_config, save_config, repair_json_quotes)
│   ├── db.py                # Database connection manager (get_db context manager)
│   ├── auth.py              # Authentication (password hashing, user CRUD, sessions)
│   ├── parser.py            # PDF/XLSX parsing with OCR support
│   ├── ocr.py               # OCR via pytesseract + vision LLM
│   ├── export_import.py     # AES-256-GCM encrypted export/import core
│   ├── extraction/           # Pluggable extraction package
│   │   ├── __init__.py      # Unified interface (extract_items_async)
│   │   ├── router.py        # Auto mode selection (scanned/text/XLSX)
│   │   ├── local.py         # Rules-based extractor
│   │   ├── llm.py           # Text LLM extractor (per-sheet for XLSX)
│   │   └── vision.py        # Vision LLM extractor (scanned PDFs)
│   ├── auto_backup.py       # Automatic backup subsystem (daily/weekly/event)
│   ├── key_manager.py       # Internal Backup Key management
│   ├── cli.py               # CLI entry point (pre-update backup, key rotation)
│   ├── routes/               # Route modules (split from main.py)
│   │   ├── __init__.py      # Route registry
│   │   ├── auth.py          # Login/logout, user management
│   │   ├── files.py         # Upload, processing, confirm, delete
│   │   ├── ai.py            # AI server connection testing
│   │   ├── export_import.py # Encrypted export/import endpoints
│   │   ├── auto_backup.py   # Auto-backup status, list, restore endpoints
│   │   └── admin.py         # Config, cleanup, search, brand suggestions
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── index.html           # HTML structure
│   ├── style.css            # All styles
│   └── js/
│       ├── utils.js         # Shared utilities (escapeHtml, formatBytes, popups, modals)
│       ├── app.js           # Global state and init
│       ├── auth.js          # Login, logout, password, roles
│       ├── nav.js           # Navigation (Process / Search / Settings)
│       ├── upload.js        # File upload & queue management
│       ├── progress.js      # SSE streaming & progress bars
│       ├── review.js        # PDF viewer & items table
│       ├── search.js        # Search, edit, delete, PDF viewer
│       ├── settings.js      # Settings, AI connection, backup/restore, logs
│       └── users.js         # User management (master only)
├── data/                    # Runtime data (gitignored, persists in Docker volume)
│   ├── quotations.db        # SQLite database with FTS5
│   ├── archive/             # Archived PDFs
│   └── temp/                # Temporary uploads
├── rules.md/                # UI system rules (layout, UX principles)
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

> **Single-node deployment:** QuoteHub runs as one container with one SQLite database. SQLite does not support concurrent writers across processes, so horizontal scaling or clustering is not possible. No failover or high-availability is planned.

- **config.json** — Mounted from host, survives rebuilds
- **data/** — Stored in Docker named volume `quodb_data`, survives container rebuilds
- **Database** — SQLite with FTS5 full-text search index
- **PDFs** — Archived in `data/archive/`

## Backup & Restore

> **Security:** All exports use AES-256-GCM encrypted `.quodb` packages. Plain ZIP/JSON imports are no longer supported.
> **Access:** Export and Import are **master-only** operations.

### Manual Backup
1. Go to **Settings** → **Backup / Restore** (top nav bar)
2. **Export**: Click **Export Backup** — a modal asks for a password (with confirmation, strength bar, and a warning to write it down). The password is **never stored**. **You must remember it** to import this backup later. There is no "forgot password" recovery.
3. **Import**: Click **Choose .quodb File** and select a previously exported `.quodb` backup. Enter the same password used during export. Optionally check **Dry Run** to preview changes without applying them.

### Automatic Backups
The system automatically creates daily encrypted backups at 03:00 (configurable). Weekly snapshots are promoted on Sundays. Event backups are triggered before imports and app updates.

- **Settings** → **Backup / Restore** shows the last and next backup time
- Click **Restore from automatic backup** to browse and restore a previous snapshot — these use a machine-bound internal key, so no password is needed for restore
- Retention: 7 daily backups, 4 weekly backups, 45 days for event backups
- Internal Backup Key hierarchy: HKDF-wrapped AES-256-GCM, machine-bound, verifiable via CLI

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
- **Extraction:** Pluggable package (local rules-based + LLM with fallback, Vision LLM for scanned PDFs)
- **Container:** Docker with Python 3.11-slim, non-root `quodb` user via `gosu`
- **File Encryption:** AES-256-GCM at rest, raw 32-byte key from `FILE_ENCRYPTION_KEY` env var
- **Backup Encryption:** AES-256-GCM with PBKDF2-600K key derivation for `.quodb` export packages
- **Auto-Backup Key:** Machine-bound HKDF-wrapped AES-256-GCM key hierarchy
- **AI Validation:** Pydantic models for LLM output; 100 KB cap on VLM responses

## License

MIT
