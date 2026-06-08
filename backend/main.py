"""
backend/main.py — Application entry point for QuoteHub.

This module creates the FastAPI app and configures middleware.
All endpoints are defined in route modules under routes/.

Modules:
    routes/auth.py: Authentication and user management
    routes/files.py: File upload, processing, and management
    routes/ai.py: AI server connection
    routes/admin.py: Configuration and system administration
    routes/debug.py: Debug and inspection endpoints
"""

import os
import json
import secrets
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .auth import DATA_DIR
from .utils import load_config, get_config_data
from .db import init_db

# ─── Version Info ────────────────────────────────────────

VERSION_PATH = Path(__file__).parent.parent / "VERSION"
GIT_COMMIT_PATH = Path(__file__).parent.parent / "GIT_COMMIT"


def read_file_text(path, default=""):
    """Read text from a file, returning default if not found."""
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return default


APP_VERSION = read_file_text(VERSION_PATH, "0.0.0")
APP_COMMIT = read_file_text(GIT_COMMIT_PATH, "unknown")

# ─── Config ───────────────────────────────────────────────

CONFIG = load_config()

# ─── State ────────────────────────────────────────────────

ai_connected = False
uploaded_files = []

# ─── Directories ──────────────────────────────────────────

UPLOAD_DIR = Path(__file__).parent.parent / "data" / "temp"
ARCHIVE_DIR = Path(__file__).parent.parent / "data" / "archive"
IMAGES_DIR = Path(__file__).parent.parent / "data" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ─── Upload State Persistence ─────────────────────────────

UPLOAD_STATE_PATH = Path(__file__).parent.parent / "data" / "upload_state.json"


def load_upload_state():
    """Restore uploaded files from previous session."""
    global uploaded_files
    if UPLOAD_STATE_PATH.exists():
        try:
            with open(UPLOAD_STATE_PATH) as f:
                saved = json.load(f)
            restored = []
            for entry in saved:
                filepath = Path(entry.get("filepath", ""))
                if filepath.exists():
                    img_dir = IMAGES_DIR / filepath.stem
                    page_files = sorted(img_dir.glob("page_*.png")) if img_dir.is_dir() else []
                    entry["pages"] = [f"/images/{filepath.stem}/{p.name}" for p in page_files]
                    entry["num_pages"] = len(page_files)
                    restored.append(entry)
            uploaded_files = restored
            print(f"Restored {len(restored)} file(s) from previous session")
        except Exception as e:
            print(f"Failed to load upload state: {e}")


def save_upload_state():
    """Save uploaded files state for persistence."""
    try:
        to_save = []
        for entry in uploaded_files:
            to_save.append({
                "filename": entry["filename"],
                "filepath": entry["filepath"],
                "status": entry["status"],
                "pages": entry.get("pages", []),
                "num_pages": entry.get("num_pages", 0),
                "progress": entry.get("progress", "")
            })
        with open(UPLOAD_STATE_PATH, "w") as f:
            json.dump(to_save, f, indent=2)
    except Exception as e:
        print(f"Failed to save upload state: {e}")


# ─── Database ─────────────────────────────────────────────

# Initialize database schema on startup
init_db()

# ─── Secret Key ───────────────────────────────────────────

SECRET_KEY_PATH = DATA_DIR / "secret.key"


def _get_or_create_secret_key():
    """Get or create session secret key."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(key)
    try:
        os.chmod(SECRET_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


SECRET_KEY = _get_or_create_secret_key()

# ─── App Factory ──────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup and shutdown events."""
    cfg = load_config()
    print(f"QuoDB starting. AI endpoint: {cfg.get('ai_endpoint', 'NOT SET')}")
    print(f"QuoDB starting. AI model: {cfg.get('model', 'NOT SET')}")
    print(f"QuoDB starting. AI connected: {ai_connected}")
    auth.bootstrap_master()
    load_upload_state()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="quotahub_session",
    same_site="lax",
    https_only=False,
    max_age=get_config_data().get("session_max_age", 14 * 24 * 60 * 60),
)

# ─── Register Routes ──────────────────────────────────────

from .routes import (
    auth_router, users_router, init_password_router,
    files_router, ai_router, admin_router, debug_router,
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(init_password_router)
app.include_router(files_router)
app.include_router(ai_router)
app.include_router(admin_router)
app.include_router(debug_router)

# ─── Static Files ─────────────────────────────────────────

app.mount("/static", StaticFiles(directory=Path(__file__).parent.parent / "frontend"), name="static")


@app.get("/")
async def root():
    """Serve the main HTML page."""
    from fastapi.responses import HTMLResponse
    html_path = Path(__file__).parent.parent / "frontend" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>QuoteHub</h1><p>Frontend not found.</p>")
