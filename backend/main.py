import os
import json
import base64
import calendar
import sqlite3
import shutil
import re
import secrets
import zipfile
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, Query, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import httpx
from pdf2image import convert_from_path
from PIL import Image
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .auth import (
    require_role, LoginRequest, ChangePasswordRequest,
    UserCreate, UserUpdate, SESSION_USER_ID, DATA_DIR,
    CONFIG_PATH,
    read_init_password, acknowledge_init_password,
)

# ─── Version Info ────────────────────────────────────────
VERSION_PATH = Path(__file__).parent.parent / "VERSION"
GIT_COMMIT_PATH = Path(__file__).parent.parent / "GIT_COMMIT"

def read_file_text(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return default

APP_VERSION = read_file_text(VERSION_PATH, "0.0.0")
APP_COMMIT = read_file_text(GIT_COMMIT_PATH, "unknown")

# ─── Config ───────────────────────────────────────────────
# CONFIG_PATH is imported from auth (single source of truth).

_CONFIG_DEFAULTS = {
    "ai_endpoint": "",
    "model": "",
    "external_url": "",
    "timeout": 120,
    "max_retries": 3,
    "popup_duration": 3,
    "session_max_age": 14 * 24 * 60 * 60,     # 14 days, in seconds
    "idle_timeout_minutes": 60,                # 60 minutes; 0 = disabled
    "llm_fallback_enabled": False,             # v0.037.0: use LLM if local returns 0 items
    "ocr_enabled": True,                        # v0.038.0: use OCR (pytesseract) for scanned PDFs
    "ocr_fallback_to_llm": True,               # v0.038.0: fall back to vision LLM if tesseract quality is low
}

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return dict(_CONFIG_DEFAULTS)

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def get_config_data():
    cfg = load_config()
    for k, v in _CONFIG_DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg

CONFIG = load_config()

# ─── State ────────────────────────────────────────────────
ai_connected = False
uploaded_files = []
db_path = Path(__file__).parent.parent / "data" / "quotations.db"
UPLOAD_STATE_PATH = Path(__file__).parent.parent / "data" / "upload_state.json"

def load_upload_state():
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
                    entry["pages"] = [p for p in entry.get("pages", []) if (Path(__file__).parent.parent / p.lstrip("/")).exists()]
                    entry["num_pages"] = len(entry["pages"])
                    restored.append(entry)
            uploaded_files = restored
            print(f"Restored {len(restored)} file(s) from previous session")
        except Exception as e:
            print(f"Failed to load upload state: {e}")

def save_upload_state():
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

# ─── Directories ──────────────────────────────────────────
UPLOAD_DIR = Path(__file__).parent.parent / "data" / "temp"
ARCHIVE_DIR = Path(__file__).parent.parent / "data" / "archive"
IMAGES_DIR = Path(__file__).parent.parent / "data" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ─── Database ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            supplier TEXT,
            quotation_date TEXT,
            items TEXT,
            document_type TEXT DEFAULT 'unknown',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migration: add document_type column to existing tables
    cols = [row[1] for row in conn.execute("PRAGMA table_info(quotations)").fetchall()]
    if "document_type" not in cols:
        conn.execute("ALTER TABLE quotations ADD COLUMN document_type TEXT DEFAULT 'unknown'")
    # Create FTS virtual table for fast search
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS quotations_fts USING fts5(
            supplier, quotation_date, items, content='quotations', content_rowid='id'
        )
    """)
    # Create triggers to keep FTS in sync
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS quotations_ai AFTER INSERT ON quotations BEGIN
            INSERT INTO quotations_fts(rowid, supplier, quotation_date, items)
            VALUES (new.id, new.supplier, new.quotation_date, new.items);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS quotations_ad AFTER DELETE ON quotations BEGIN
            INSERT INTO quotations_fts(quotations_fts, rowid, supplier, quotation_date, items)
            VALUES ('delete', old.id, old.supplier, old.quotation_date, old.items);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS quotations_au AFTER UPDATE ON quotations BEGIN
            INSERT INTO quotations_fts(quotations_fts, rowid, supplier, quotation_date, items)
            VALUES ('delete', old.id, old.supplier, old.quotation_date, old.items);
            INSERT INTO quotations_fts(rowid, supplier, quotation_date, items)
            VALUES (new.id, new.supplier, new.quotation_date, new.items);
        END
    """)
    # Populate FTS table with existing data if empty
    count = conn.execute("SELECT COUNT(*) FROM quotations_fts").fetchone()[0]
    if count == 0:
        conn.execute("""
            INSERT INTO quotations_fts(rowid, supplier, quotation_date, items)
            SELECT id, supplier, quotation_date, items FROM quotations
        """)
    # Users table for auth (master/admin/user)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('master', 'admin', 'user')),
            must_change_password INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ─── App ──────────────────────────────────────────────────
# ─── Session secret key (persisted in data volume) ────────
SECRET_KEY_PATH = DATA_DIR / "secret.key"

def _get_or_create_secret_key():
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

@asynccontextmanager
async def lifespan(app: FastAPI):
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

# ─── Models ───────────────────────────────────────────────
class ProcessRequest(BaseModel):
    file_index: int

class ConfirmRequest(BaseModel):
    file_index: int
    data: dict

class ConfigRequest(BaseModel):
    ai_endpoint: str
    model: str
    external_url: str = ""
    timeout: int = 180
    max_retries: int = 2
    popup_duration: int = 3
    session_max_age: int = 14 * 24 * 60 * 60
    idle_timeout_minutes: int = 60
    llm_fallback_enabled: bool = False
    ocr_enabled: bool = True
    ocr_fallback_to_llm: bool = True

class CleanupPreviewRequest(BaseModel):
    months: int = Field(ge=1, le=120)   # 1 month to 10 years

class CleanupExecuteRequest(BaseModel):
    months: int = Field(ge=1, le=120)
    delete_files: bool = False

# ─── Config Endpoints ─────────────────────────────────────
# GET is allowed for admin+master (admin can READ settings to populate the
# Settings modal). POST is master-only (only master can change AI settings).
@app.get("/config", dependencies=[Depends(require_role("admin", "master"))])
async def get_config_route():
    return get_config_data()

@app.post("/config", dependencies=[Depends(require_role("master"))])
async def update_config(req: ConfigRequest):
    cfg = get_config_data()
    cfg["ai_endpoint"] = req.ai_endpoint
    cfg["model"] = req.model
    cfg["external_url"] = req.external_url
    cfg["timeout"] = req.timeout
    cfg["max_retries"] = req.max_retries
    cfg["popup_duration"] = req.popup_duration
    cfg["session_max_age"] = req.session_max_age
    cfg["idle_timeout_minutes"] = req.idle_timeout_minutes
    cfg["llm_fallback_enabled"] = req.llm_fallback_enabled
    cfg["ocr_enabled"] = req.ocr_enabled
    cfg["ocr_fallback_to_llm"] = req.ocr_fallback_to_llm
    save_config(cfg)
    return {"status": "saved", "config": cfg}

# ─── Version Endpoint ─────────────────────────────────────
@app.get("/version")
async def get_version():
    return {
        "version": APP_VERSION,
        "commit": APP_COMMIT
    }

# ─── Auth Endpoints ───────────────────────────────────────
@app.post("/auth/login")
async def login(req: LoginRequest, request: Request):
    user = auth.get_user_by_username(req.username)
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.get("active"):
        raise HTTPException(status_code=401, detail="Account is disabled")
    request.session[SESSION_USER_ID] = user["id"]
    auth.record_login(user["id"])
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "must_change_password": user["must_change_password"],
    }

@app.post("/auth/logout")
async def logout(request: Request):
    request.session.pop(SESSION_USER_ID, None)
    return {"status": "logged_out"}

@app.get("/auth/me")
async def me(request: Request):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "must_change_password": user["must_change_password"],
    }

@app.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, request: Request):
    user = auth.get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not auth.verify_password(req.old_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    auth.update_user_password(user["id"], req.new_password)
    auth.clear_must_change_password(user["id"])
    # If the init file still exists, auto-delete it now that the master
    # has set a real password.
    acknowledge_init_password()
    return {"status": "changed"}

# ─── User Management (master only) ────────────────────────
@app.get("/users", dependencies=[Depends(require_role("master"))])
async def list_users_route():
    return auth.list_users()

@app.post("/users", dependencies=[Depends(require_role("master"))])
async def create_user_route(req: UserCreate):
    if auth.get_user_by_username(req.username):
        raise HTTPException(status_code=400, detail="Username already exists")
    user_id = auth.create_user(req.username, req.password, req.role)
    return {"id": user_id, "status": "created"}

@app.patch("/users/{user_id}", dependencies=[Depends(require_role("master"))])
async def update_user_route(user_id: int, req: UserUpdate, user: dict = Depends(require_role("master"))):
    target = auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    # Block self-deactivation (would lock the master out)
    if req.active is False and user_id == user["id"]:
        raise HTTPException(status_code=400, detail="cannot_deactivate_self")
    # Block deactivation if it would leave no active master
    if req.active is False and target["role"] == "master" and auth.count_active_masters() <= 1:
        raise HTTPException(status_code=400, detail="cannot_deactivate_last_master")
    if req.role:
        auth.update_user_role(user_id, req.role)
    if req.new_password:
        auth.update_user_password(user_id, req.new_password)
    if req.active is not None:
        auth.set_user_active(user_id, req.active)
    return {"status": "updated"}

@app.delete("/users/{user_id}", dependencies=[Depends(require_role("master"))])
async def delete_user_route(
    user_id: int,
    hard: bool = Query(False, description="If true, permanently delete the user instead of deactivating"),
    user: dict = Depends(require_role("master")),
):
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="cannot_delete_self")
    target = auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if hard:
        # Hard delete: protect against removing the last master of any status
        if target["role"] == "master" and auth.count_masters() <= 1:
            raise HTTPException(status_code=400, detail="cannot_delete_last_master")
        auth.hard_delete_user(user_id)
        return {"status": "deleted_permanently"}
    else:
        # Soft delete: protect against removing the last ACTIVE master
        if target["role"] == "master" and auth.count_active_masters() <= 1:
            raise HTTPException(status_code=400, detail="cannot_delete_last_master")
        auth.soft_delete_user(user_id)
        return {"status": "deactivated"}

@app.get("/init-password/status", dependencies=[Depends(require_role("master"))])
async def init_password_status(user: dict = Depends(require_role("master"))):
    # Only callable when the master still needs to change the password
    if not user["must_change_password"]:
        raise HTTPException(status_code=404, detail="Not available")
    password = read_init_password()
    if password is None:
        raise HTTPException(status_code=404, detail="No init password file")
    return {"password": password}

@app.post("/init-password/acknowledge", dependencies=[Depends(require_role("master"))])
async def init_password_acknowledge():
    deleted = acknowledge_init_password()
    return {"deleted": deleted}

# ─── AI Connection ────────────────────────────────────────
@app.post("/ai/connect", dependencies=[Depends(require_role("admin", "master"))])
async def ai_connect():
    global ai_connected
    cfg = load_config()
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    timeout = cfg.get("timeout", 30)

    if not endpoint:
        return {"status": "failed", "error": "AI endpoint not configured. Please set it in Settings."}
    if not model:
        return {"status": "failed", "error": "Model not configured. Please set it in Settings."}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                endpoint,
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": "Respond with OK"}
                    ],
                    "max_tokens": 10
                }
            )
            if resp.status_code == 200:
                ai_connected = True
                return {"status": "connected"}
            else:
                return {"status": "failed", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except httpx.ConnectError as e:
        return {"status": "failed", "error": f"Cannot reach server: {str(e)}"}
    except httpx.TimeoutException:
        return {"status": "failed", "error": "Connection timed out"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}

@app.get("/ai/status", dependencies=[Depends(require_role("admin", "master"))])
async def ai_status():
    return {"connected": ai_connected}

# ─── Upload ───────────────────────────────────────────────
ALLOWED_FILE_EXTENSIONS = (".pdf", ".xlsx")

@app.post("/upload", dependencies=[Depends(require_role("admin", "master"))])
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(ALLOWED_FILE_EXTENSIONS):
        return JSONResponse(
            status_code=400,
            content={"error": f"Only {', '.join(ALLOWED_FILE_EXTENSIONS)} files allowed"},
        )

    filepath = UPLOAD_DIR / Path(file.filename).name
    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    # Convert PDF pages to images. Skip for .xlsx files (no pages to render).
    page_images = []
    if file.filename.lower().endswith(".pdf"):
        try:
            images = convert_from_path(str(filepath), dpi=300)
            file_dir = IMAGES_DIR / Path(file.filename).stem
            file_dir.mkdir(parents=True, exist_ok=True)
            for i, img in enumerate(images):
                img_path = file_dir / f"page_{i+1}.png"
                img.save(str(img_path), "PNG")
                page_images.append(f"/images/{Path(file.filename).stem}/page_{i+1}.png")
        except Exception as e:
            print(f"PDF conversion error: {e}")
        if filepath.exists():
            filepath.unlink()
        return JSONResponse(status_code=400, content={"error": "PDF conversion failed"})

    # Reject PDFs that yielded 0 pages (corrupt files that did not raise)
    if len(page_images) == 0:
        try:
            if filepath.exists():
                filepath.unlink()
        except OSError:
            pass
        return JSONResponse(status_code=400, content={"error": "PDF conversion failed"})

    entry = {
        "filename": file.filename,
        "filepath": str(filepath),
        "status": "pending",
        "pages": page_images,
        "num_pages": len(page_images)
    }
    uploaded_files.append(entry)
    save_upload_state()
    file_index = len(uploaded_files) - 1
    return {"filename": file.filename, "status": "pending", "pages": len(page_images), "file_index": file_index}

@app.post("/clear", dependencies=[Depends(require_role("admin", "master"))])
async def clear_files():
    global uploaded_files
    uploaded_files = []
    save_upload_state()
    return {"status": "cleared"}

# ─── Next File (PDF preview) ──────────────────────────────
@app.get("/next-file", dependencies=[Depends(require_role("admin", "master"))])
async def next_file(file_index: int = 0):
    if file_index >= len(uploaded_files):
        return {"pages": []}
    return {"pages": uploaded_files[file_index]["pages"]}

# ─── Process ──────────────────────────────────────────────
def repair_json_quotes(raw):
    """Fix unescaped double quotes inside JSON string values."""
    result = []
    in_string = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == '"' and (i == 0 or raw[i-1] != '\\'):
            if not in_string:
                in_string = True
                result.append(ch)
            else:
                rest = raw[i+1:i+20].lstrip()
                if rest and rest[0] in ':,}]\n':
                    in_string = False
                    result.append(ch)
                else:
                    result.append('\\"')
        else:
            result.append(ch)
        i += 1
    return ''.join(result)

def compress_image(img_path, max_width=1280, quality=80):
    """Compress image for AI processing while keeping it readable."""
    img = Image.open(img_path)
    if img.width > max_width:
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.LANCZOS)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    out = Path(img_path).with_suffix(".jpg")
    img.save(str(out), "JPEG", quality=quality, optimize=True)
    return str(out)

async def call_ai(image_paths, page_num, file_stem):
    cfg = load_config()
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    timeout = cfg.get("timeout", 180)
    max_retries = cfg.get("max_retries", 2)
    external_url = cfg.get("external_url", "").rstrip("/")

    if not endpoint or not model:
        return None, "AI endpoint or model not configured"

    prompt = f"""Analyze this document image. Classify the document type AND extract ALL information. Return ONLY valid JSON.

STRICT ITEM FILTERING:
- A valid item MUST have a model (part number) AND a numeric unit price.
- Ignore rows that contain "Optional", "no need", missing/non-numeric price, or are category/group headers.

MODEL RULES (CRITICAL):
- Extract ONLY ONE model per item. If multiple, use ONLY the primary; ignore optional alternatives.
- "change to X" → use ONLY X. "include in ..." → IGNORE the entire row.

ROW STRUCTURE RULE:
- Each item = ONE table row. DO NOT merge, mix, or infer values from adjacent rows.

BRAND RULES:
- Brand must be a real manufacturer name. DO NOT use category headers, group names, or descriptions. If no brand → leave empty.

DESCRIPTION RULES:
- Copy full description exactly. Merge multiline into ONE field. Do NOT shorten.
- Inch marks: replace " with ' (e.g. 10.1" → 10.1').

DOCUMENT TYPE CLASSIFICATION:
- "QUO" = Quotation (price offer from supplier to client)
- "PO"  = Purchase Order (order issued by client to supplier)
- "PL"  = Price List (catalog or list of products with prices)
- If uncertain → "unknown" (default)

FIELD NORMALIZATION:
- PRICE:
  - Extract numeric value only.
  - MUST be formatted with: comma as thousand separator, period as decimal separator.
  - Always 2 decimal places.
  - REQUIRED format: X,XXX.XX
  - Examples: 1157 → 1,157.00, 15700 → 15,700.00, 99.5 → 99.50
  - Remove ALL currency symbols (e.g. $, HK$, €, £)
  - DO NOT output: 1157.00, 1.157,00, $1,157.00
- CURRENCY:
  - If document explicitly states currency (e.g. "Unit Price (HKD)") → use it for ALL items
  - "$" inherits the document currency (e.g. HKD in this case)
  - Only use USD if explicitly stated
  - Otherwise infer from document context (header or table title)
  - Use ISO 4217 codes (HKD, USD, EUR, etc.)
- DATE: Always convert to YYYY-MM-DD. Example: 20/1/2026 → 2026-01-20, "January 20 2026" → 2026-01-20.

FINAL STRICT RULE:
- If an item does not meet ALL rules above → SKIP it completely.

Return this exact structure:
{{
  "document_type": "QUO" | "PO" | "PL" | "unknown",
  "supplier": "full company name issuing this quotation",
  "items": [
    {{
      "brand": "product brand/manufacturer",
      "model": "product model or part number",
      "description": "full description copied exactly from document",
      "unit_price": "formatted price: X,XXX.XX",
      "currency": "ISO 4217 code: USD, EUR, HKD, GBP, JPY, CNY, MOP, etc.",
      "date": "date in YYYY-MM-DD format"
    }}
  ]
}}

Return ONLY valid parseable JSON, no markdown, no explanation"""

    content_parts = []
    for i, img_path in enumerate(image_paths):
        if external_url and "localhost" not in external_url and "127.0.0.1" not in external_url:
            rel_path = f"/images/{file_stem}/page_{page_num + 1}.png"
            url = f"{external_url}{rel_path}"
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
        else:
            compressed = compress_image(img_path)
            with open(compressed, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })
        if i == 0:
            content_parts.append({"type": "text", "text": prompt})
        else:
            content_parts.append({"type": "text", "text": f"Page {i + 1} - continue extracting all items"})

    messages = [{"role": "user", "content": content_parts}]

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=timeout, connect=5.0)) as client:
                resp = await client.post(
                    endpoint,
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": 2048,
                        "temperature": 0.1
                    }
                )
                if resp.status_code == 200:
                    try:
                        result = resp.json()
                        if "choices" not in result or not result.get("choices"):
                            return None, "AI returned no choices"
                        msg = result["choices"][0]["message"]
                        content = msg.get("content") or ""
                        reasoning = msg.get("reasoning_content") or ""
                        raw = content or reasoning
                        raw = raw.strip()
                        if raw.startswith("```"):
                            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                        if raw.endswith("```"):
                            raw = raw[:-3]
                        raw = raw.strip()
                        print(f"AI response ({len(raw)} chars): {raw[:300]}")
                        start = raw.find("{")
                        end = raw.rfind("}") + 1
                        if start != -1:
                            json_str = raw[start:end] if end > start else raw[start:]
                            if end <= start:
                                json_str = json_str.rstrip(",") + "]}"
                            try:
                                parsed = json.loads(json_str)
                                print(f"Parse OK (direct): {json.dumps(parsed, indent=2)[:500]}")
                                return parsed, None
                            except json.JSONDecodeError as e:
                                print(f"Parse FAIL (direct): {e}")
                            repaired = repair_json_quotes(json_str)
                            try:
                                parsed = json.loads(repaired)
                                print(f"Parse OK (repaired): {json.dumps(parsed, indent=2)[:500]}")
                                return parsed, None
                            except json.JSONDecodeError as e:
                                print(f"Parse FAIL (repaired): {e}")
                            for suffix in ["}", "\"}]}", "}]}", "]}"]:
                                try:
                                    repaired = json_str.rstrip(",") + suffix
                                    return json.loads(repaired), None
                                except json.JSONDecodeError:
                                    continue
                        return None, f"No valid JSON. Response: {raw[:200]}"
                    except (json.JSONDecodeError, KeyError, IndexError) as e:
                        return None, f"Error parsing AI response: {str(e)}"
                else:
                    print(f"AI error (attempt {attempt + 1}): {resp.status_code} - {resp.text[:300]}")
                    return None, f"AI returned HTTP {resp.status_code}: {resp.text[:300]}"
        except httpx.ConnectError as e:
            print(f"Connection error (attempt {attempt + 1}): {e}")
        except httpx.TimeoutException:
            print(f"Timeout (attempt {attempt + 1})")
        except Exception as e:
            print(f"Error (attempt {attempt + 1}): {e}")

        if attempt < max_retries - 1:
            print(f"Retrying... ({attempt + 1}/{max_retries})")

    return None, "All retries exhausted"

@app.post("/process-all", dependencies=[Depends(require_role("admin", "master"))])
async def process_all(req: ProcessRequest):
    """Process ALL pages of a file sequentially, merge results."""
    global ai_connected
    if not ai_connected:
        return JSONResponse(status_code=400, content={"status": "error", "error": "AI not connected. Please connect first."})

    if req.file_index >= len(uploaded_files):
        return JSONResponse(status_code=400, content={"status": "error", "error": "File not found"})

    file_entry = uploaded_files[req.file_index]
    img_dir = IMAGES_DIR / Path(file_entry["filename"]).stem
    file_stem = Path(file_entry["filename"]).stem

    all_items = []
    supplier = ""
    document_type = ""
    shared_date = ""
    errors = []

    for page_idx in range(file_entry["num_pages"]):
        img_path = img_dir / f"page_{page_idx + 1}.png"
        if not img_path.exists():
            continue

        image_paths = [str(img_path)]
        result, error = await call_ai(image_paths, page_idx, file_stem)

        if result is None:
            errors.append(f"Page {page_idx + 1}: {error}")
            continue

        # Take supplier from first page that has it
        if not supplier and result.get("supplier"):
            supplier = result["supplier"]

        # Take document_type from first page that has it
        if not document_type and result.get("document_type"):
            document_type = result["document_type"]

        # Find date from any page
        if not shared_date:
            for item in result.get("items", []):
                if item.get("date"):
                    shared_date = item["date"]
                    break

        # Merge items from this page
        page_items = result.get("items", [])
        all_items.extend(page_items)
        print(f"Page {page_idx + 1}: extracted {len(page_items)} items")

    # Apply shared date to all items that don't have one
    if shared_date:
        for item in all_items:
            if not item.get("date"):
                item["date"] = shared_date

    # Coerce document_type to one of the valid values
    if document_type not in ("QUO", "PO", "PL", "unknown"):
        document_type = "unknown"

    if not all_items and errors:
        return JSONResponse(status_code=500, content={"status": "error", "error": "; ".join(errors)})

    merged = {
        "supplier": supplier,
        "document_type": document_type,
        "items": all_items
    }

    uploaded_files[req.file_index]["status"] = "processed"
    save_upload_state()
    return {"status": "success", "data": merged, "pages_processed": file_entry["num_pages"], "errors": errors}

@app.post("/process-stream", dependencies=[Depends(require_role("admin", "master"))])
async def process_stream(req: ProcessRequest):
    """Process ALL pages with SSE streaming progress updates."""
    import asyncio
    from fastapi.responses import StreamingResponse

    global ai_connected
    if not ai_connected:
        return JSONResponse(status_code=400, content={"status": "error", "error": "AI not connected."})

    if req.file_index >= len(uploaded_files):
        return JSONResponse(status_code=400, content={"status": "error", "error": "File not found"})

    file_entry = uploaded_files[req.file_index]
    img_dir = IMAGES_DIR / Path(file_entry["filename"]).stem
    file_stem = Path(file_entry["filename"]).stem
    total_pages = file_entry["num_pages"]

    async def generate():
        all_items = []
        supplier = ""
        document_type = ""
        shared_date = ""
        errors = []

        if total_pages == 0:
            uploaded_files[req.file_index]["status"] = "error"
            save_upload_state()
            yield f"data: {json.dumps({'type': 'done', 'data': {'supplier': '', 'document_type': 'unknown', 'items': []}, 'pages_processed': 0, 'errors': ['No pages to process']})}\n\n"
            return

        for page_idx in range(total_pages):
            # Send progress: processing this page
            progress = int(((page_idx) / total_pages) * 100)
            yield f"data: {json.dumps({'type': 'progress', 'page': page_idx + 1, 'total': total_pages, 'percent': progress, 'message': f'Processing page {page_idx + 1} of {total_pages}...'})}\n\n"

            img_path = img_dir / f"page_{page_idx + 1}.png"
            if not img_path.exists():
                errors.append(f"Page {page_idx + 1}: image not found")
                continue

            image_paths = [str(img_path)]
            result, error = await call_ai(image_paths, page_idx, file_stem)

            if result is None:
                errors.append(f"Page {page_idx + 1}: {error}")
                yield f"data: {json.dumps({'type': 'page_error', 'page': page_idx + 1, 'error': error})}\n\n"
                continue

            if not supplier and result.get("supplier"):
                supplier = result["supplier"]

            if not document_type and result.get("document_type"):
                document_type = result["document_type"]

            if not shared_date:
                for item in result.get("items", []):
                    if item.get("date"):
                        shared_date = item["date"]
                        break

            page_items = result.get("items", [])
            # Add page number to each item
            for item in page_items:
                item["page"] = page_idx + 1
            all_items.extend(page_items)

            # Send progress: page done
            page_done_percent = int(((page_idx + 1) / total_pages) * 100)
            yield f"data: {json.dumps({'type': 'page_done', 'page': page_idx + 1, 'total': total_pages, 'percent': page_done_percent, 'items_found': len(page_items)})}\n\n"
            print(f"Page {page_idx + 1}: extracted {len(page_items)} items")

            # Small yield to allow client to process
            await asyncio.sleep(0)

        # Apply shared date to all items
        if shared_date:
            for item in all_items:
                if not item.get("date"):
                    item["date"] = shared_date

        # Coerce document_type to one of the valid values
        if document_type not in ("QUO", "PO", "PL", "unknown"):
            document_type = "unknown"

        merged = {"supplier": supplier, "document_type": document_type, "items": all_items}
        if all_items:
            uploaded_files[req.file_index]["status"] = "processed"
        elif errors:
            uploaded_files[req.file_index]["status"] = "error"
        else:
            uploaded_files[req.file_index]["status"] = "processed"
        save_upload_state()

        # Send final result
        yield f"data: {json.dumps({'type': 'done', 'data': merged, 'pages_processed': total_pages, 'errors': errors})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ─── Confirm / Save ───────────────────────────────────────
@app.post("/confirm", dependencies=[Depends(require_role("admin", "master"))])
async def confirm(req: ConfirmRequest):
    if req.file_index < 0 or req.file_index >= len(uploaded_files):
        raise HTTPException(status_code=404, detail="File not found")
    if uploaded_files[req.file_index]["status"] != "processed":
        raise HTTPException(status_code=400, detail="File must be processed before confirming")
    data = req.data
    items = data.get("items", [])
    supplier = data.get("supplier", "") or (items[0].get("supplier", "") if items else "")
    quotation_date = items[0].get("date", "") if items else ""
    document_type = data.get("document_type", "unknown")
    if document_type not in ("QUO", "PO", "PL"):
        raise HTTPException(status_code=400, detail="document_type must be QUO, PO, or PL")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO quotations (filename, supplier, quotation_date, items, document_type) VALUES (?, ?, ?, ?, ?)",
        (uploaded_files[req.file_index]["filename"],
         supplier,
         quotation_date,
         json.dumps(items),
         document_type)
    )
    last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    # Move PDF to archive
    src = Path(uploaded_files[req.file_index]["filepath"])
    if src.exists():
        dst = ARCHIVE_DIR / src.name
        shutil.move(str(src), str(dst))

    # Clean up images
    file_stem = Path(uploaded_files[req.file_index]["filename"]).stem
    img_dir = IMAGES_DIR / file_stem
    if img_dir.exists():
        shutil.rmtree(str(img_dir))

    uploaded_files[req.file_index]["status"] = "saved"
    save_upload_state()

    return {"status": "saved", "id": last_id}

# ─── Skip ─────────────────────────────────────────────────
@app.post("/skip", dependencies=[Depends(require_role("admin", "master"))])
async def skip(req: ProcessRequest):
    uploaded_files[req.file_index]["status"] = "skipped"
    save_upload_state()
    return {"status": "skipped"}

class DeleteRequest(BaseModel):
    ids: list[int]

class UpdateRequest(BaseModel):
    id: int
    data: dict

@app.post("/delete", dependencies=[Depends(require_role("admin", "master"))])
async def delete(req: DeleteRequest):
    if not req.ids:
        return {"status": "nothing to delete"}
    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(req.ids))
    rows = conn.execute(f"SELECT filename FROM quotations WHERE id IN ({placeholders})", req.ids).fetchall()
    conn.execute(f"DELETE FROM quotations WHERE id IN ({placeholders})", req.ids)
    conn.commit()
    conn.close()
    for row in rows:
        archive_path = ARCHIVE_DIR / row[0]
        try:
            if archive_path.exists():
                archive_path.unlink()
        except OSError:
            pass
    return {"status": "deleted", "count": len(req.ids)}

@app.post("/update", dependencies=[Depends(require_role("admin", "master"))])
async def update_quotation(req: UpdateRequest):
    data = req.data
    items = data.get("items", [])
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    supplier = data.get("supplier", "")
    quotation_date = items[0].get("date", "") if items else ""
    document_type = data.get("document_type", "unknown")
    if document_type not in ("QUO", "PO", "PL"):
        raise HTTPException(status_code=400, detail="document_type must be QUO, PO, or PL")
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "UPDATE quotations SET supplier=?, quotation_date=?, items=?, document_type=? WHERE id=?",
        (supplier, quotation_date, json.dumps(items), document_type, req.id)
    )
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return {"status": "updated"}

# ─── Export / Import ──────────────────────────────────────
@app.get("/export", dependencies=[Depends(require_role("admin", "master"))])
async def export_db():
    """Export all quotations and archived PDFs as a zip file."""
    from fastapi.responses import StreamingResponse
    import io

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM quotations ORDER BY created_at DESC").fetchall()
    conn.close()

    data = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("items"), str):
            try:
                d["items"] = json.loads(d["items"])
            except (json.JSONDecodeError, TypeError):
                d["items"] = []
        data.append(d)

    # Create zip as temp file (not in memory)
    import tempfile
    zip_fd, zip_path = tempfile.mkstemp(suffix='.zip')
    os.close(zip_fd)
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("quotations.json", json.dumps({"quotations": data, "count": len(data)}, indent=2))
            if ARCHIVE_DIR.exists():
                for pdf_file in ARCHIVE_DIR.glob("*.pdf"):
                    zf.write(pdf_file, f"archive/{pdf_file.name}")

        zip_size = os.path.getsize(zip_path)
        filename = f"quodb_backup_{__import__('datetime').datetime.now().strftime('%Y-%m-%d')}.zip"

        def stream_file():
            with open(zip_path, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk
            os.unlink(zip_path)  # Clean up temp file after streaming

        return StreamingResponse(
            stream_file(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(zip_size)
            }
        )
    except Exception:
        if os.path.exists(zip_path):
            os.unlink(zip_path)
        raise

@app.post("/import/upload", dependencies=[Depends(require_role("admin", "master"))])
async def import_upload(file: UploadFile = File(...)):
    """Import quotations from a JSON or ZIP file."""
    content = await file.read()

    quotations = []
    pdf_restored = 0

    if file.filename.endswith(".zip"):
        # Import from zip
        import io
        try:
            with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
                # Read quotations.json
                if "quotations.json" in zf.namelist():
                    data = json.loads(zf.read("quotations.json"))
                    quotations = data.get("quotations", [])
                # Restore PDFs
                ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                for name in zf.namelist():
                    if name.startswith("archive/") and name.endswith(".pdf"):
                        pdf_name = Path(name.split("/", 1)[1]).name
                        pdf_data = zf.read(name)
                        pdf_path = ARCHIVE_DIR / pdf_name
                        with open(pdf_path, "wb") as f:
                            f.write(pdf_data)
                        pdf_restored += 1
        except zipfile.BadZipFile:
            return JSONResponse(status_code=400, content={"error": "Invalid or corrupt zip file"})
    elif file.filename.endswith(".json"):
        # Import from JSON (legacy format, no PDFs)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON file"})
        quotations = data.get("quotations", [])
    else:
        return JSONResponse(status_code=400, content={"error": "Unsupported file type. Use .zip or .json"})

    if not quotations:
        return JSONResponse(status_code=400, content={"error": "No quotations found in file"})

    conn = sqlite3.connect(db_path)
    imported = 0
    for q in quotations:
        supplier = q.get("supplier", "")
        quotation_date = q.get("quotation_date", "")
        document_type = q.get("document_type", "unknown")
        if document_type not in ("QUO", "PO", "PL", "unknown"):
            document_type = "unknown"
        items = q.get("items", [])
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except (json.JSONDecodeError, TypeError):
                items = []
        if not quotation_date and items:
            quotation_date = items[0].get("date", "")
        conn.execute(
            "INSERT INTO quotations (filename, supplier, quotation_date, items, document_type) VALUES (?, ?, ?, ?, ?)",
            (q.get("filename", "imported.pdf"), supplier, quotation_date, json.dumps(items), document_type)
        )
        imported += 1
    conn.commit()
    conn.close()
    return {"status": "imported", "count": imported, "pdfs_restored": pdf_restored}

# ─── System Cleanup (master only) ────────────────────────
def _cleanup_cutoff(months: int) -> str:
    """Return ISO date (YYYY-MM-DD) for 'X months ago'.
    Calendar-month accurate. Day is clamped to the last day of the target month."""
    now = datetime.now()
    year = now.year
    month = now.month - months
    while month <= 0:
        month += 12
        year -= 1
    # Clamp day to last valid day of target month (e.g. Mar 31 - 1 month → Feb 28/29)
    day = now.day
    last_day = calendar.monthrange(year, month)[1]
    if day > last_day:
        day = last_day
    cutoff = now.replace(year=year, month=month, day=day)
    return cutoff.date().isoformat()

def _get_old_quotations(conn, cutoff_date: str) -> list:
    """Return list of (id, filename) for quotations older than cutoff_date."""
    rows = conn.execute(
        "SELECT id, filename FROM quotations WHERE date(created_at) < ?",
        (cutoff_date,)
    ).fetchall()
    return [(row["id"], row["filename"]) for row in rows]

@app.post("/cleanup/preview", dependencies=[Depends(require_role("master"))])
async def cleanup_preview(req: CleanupPreviewRequest):
    """Preview what would be deleted: counts + total file size. Does NOT delete."""
    cutoff_date = _cleanup_cutoff(req.months)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _get_old_quotations(conn, cutoff_date)
        entries = len(rows)
        file_count = 0
        total_size = 0
        seen = set()
        for _id, filename in rows:
            if not filename or filename in seen:
                continue
            seen.add(filename)
            pdf_path = ARCHIVE_DIR / filename
            if pdf_path.exists():
                file_count += 1
                try:
                    total_size += pdf_path.stat().st_size
                except OSError:
                    pass
        return {
            "months": req.months,
            "cutoff_date": cutoff_date,
            "entries": entries,
            "files": file_count,
            "estimated_size": total_size,
        }
    finally:
        conn.close()

@app.post("/cleanup/execute", dependencies=[Depends(require_role("master"))])
async def cleanup_execute(req: CleanupExecuteRequest):
    """Delete old quotations. Optionally also delete their files + images.
    Runs VACUUM after commit to reclaim disk space."""
    cutoff_date = _cleanup_cutoff(req.months)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = _get_old_quotations(conn, cutoff_date)
        # Collect unique filenames (and their paths) BEFORE deleting rows
        seen = set()
        targets = []
        for _id, filename in rows:
            if filename and filename not in seen:
                seen.add(filename)
                pdf_path = ARCHIVE_DIR / filename
                img_dir = IMAGES_DIR / Path(filename).stem
                targets.append((filename, pdf_path, img_dir))
        # DB delete in a single transaction (FTS triggers clean up FTS)
        cur = conn.execute(
            "DELETE FROM quotations WHERE date(created_at) < ?",
            (cutoff_date,)
        )
        entries_deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    # File deletion runs AFTER DB commit (best-effort; never rolls back DB)
    file_count_deleted = 0
    bytes_freed = 0
    if req.delete_files:
        for _filename, pdf_path, img_dir in targets:
            try:
                if pdf_path.exists():
                    bytes_freed += pdf_path.stat().st_size
                    pdf_path.unlink()
                    file_count_deleted += 1
            except OSError:
                pass  # missing or locked — skip
            try:
                if img_dir.exists():
                    shutil.rmtree(img_dir, ignore_errors=True)
            except OSError:
                pass
    # Reclaim disk space (VACUUM must run outside a transaction)
    try:
        vacuum_conn = sqlite3.connect(db_path)
        vacuum_conn.execute("VACUUM")
        vacuum_conn.close()
    except Exception as e:
        print(f"VACUUM warning: {e}")
    return {
        "status": "completed",
        "entries_deleted": entries_deleted,
        "files_deleted": file_count_deleted,
        "bytes_freed": bytes_freed,
    }

# ─── Search ───────────────────────────────────────────────
@app.get("/search", dependencies=[Depends(require_role("user", "admin", "master"))])
async def search(q: str = ""):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if q:
        words = q.strip().split()
        # Build FTS match query with prefix matching (amp* matches amplifier)
        fts_words = [w.replace('"', '""') + '*' for w in words]
        fts_query = " ".join(fts_words)

        try:
            # Use FTS for fast search
            rows = conn.execute(
                """SELECT q.* FROM quotations q
                   INNER JOIN quotations_fts fts ON q.id = fts.rowid
                   WHERE quotations_fts MATCH ?
                   ORDER BY q.created_at DESC""",
                (fts_query,)
            ).fetchall()
        except sqlite3.OperationalError:
            # Fallback to LIKE if FTS fails (e.g., special characters)
            if len(words) == 1:
                rows = conn.execute(
                    "SELECT * FROM quotations WHERE supplier LIKE ? OR quotation_date LIKE ? OR items LIKE ? ORDER BY created_at DESC",
                    (f"%{words[0]}%", f"%{words[0]}%", f"%{words[0]}%")
                ).fetchall()
            else:
                conditions = []
                params = []
                for word in words:
                    conditions.append("(supplier LIKE ? OR quotation_date LIKE ? OR items LIKE ?)")
                    params.extend([f"%{word}%", f"%{word}%", f"%{word}%"])
                where_clause = " AND ".join(conditions)
                rows = conn.execute(
                    f"SELECT * FROM quotations WHERE {where_clause} ORDER BY created_at DESC",
                    params
                ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM quotations ORDER BY created_at DESC").fetchall()
    conn.close()

    q_lower = q.lower() if q else ""
    words = q_lower.split() if q_lower else []
    results = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("items"), str):
            try:
                d["items"] = json.loads(d["items"])
            except (json.JSONDecodeError, TypeError):
                d["items"] = []
        # Item-level filtering: ALL words must match
        if words and d.get("items"):
            filtered = []
            for item in d["items"]:
                searchable = " ".join([
                    str(item.get("brand", "")),
                    str(item.get("model", "")),
                    str(item.get("description", "")),
                    str(item.get("unit_price", "")),
                    str(item.get("price", "")),
                    str(item.get("date", "")),
                    str(item.get("currency", "")),
                    str(item.get("supplier", "")),
                    d.get("supplier", ""),
                    d.get("quotation_date", "")
                ]).lower()
                if all(w in searchable for w in words):
                    filtered.append(item)
            d["items"] = filtered
        results.append(d)
    return results

# ─── Brand Suggestion ─────────────────────────────────────
@app.get("/items/by-model", dependencies=[Depends(require_role("user", "admin", "master"))])
async def items_by_model(model: str = ""):
    """Return the most frequent brand for items matching a given model.
    Brands are normalized (lowercased) before counting to treat case
    variations ('Sony' and 'SONY') as the same brand, but the returned
    brand preserves the most common original casing.
    """
    from collections import Counter
    if not model.strip():
        return {"model": model, "brand": None, "count": 0}

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT items FROM quotations").fetchall()
    conn.close()
    # NOTE: may optimize later using indexed model field if DB grows

    model_lc = model.strip().lower()
    # Group original brand strings by their lowercased key
    normalized_groups = {}  # e.g. {"sony": ["Sony", "SONY", "Sony"]}
    for r in rows:
        items_raw = r["items"]
        if not items_raw:
            continue
        try:
            items = json.loads(items_raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if (item.get("model") or "").strip().lower() == model_lc:
                brand = (item.get("brand") or "").strip()
                if brand:
                    key = brand.lower()
                    normalized_groups.setdefault(key, []).append(brand)

    if not normalized_groups:
        return {"model": model, "brand": None, "count": 0}

    # Find the normalized group with the largest count
    most_common_key = max(normalized_groups, key=lambda k: len(normalized_groups[k]))
    candidates = normalized_groups[most_common_key]
    # Return the most common original casing
    most_common_brand = Counter(candidates).most_common(1)[0][0]
    return {"model": model, "brand": most_common_brand, "count": len(candidates)}

# ─── Duplicate Check ──────────────────────────────────────
@app.get("/check-duplicate", dependencies=[Depends(require_role("user", "admin", "master"))])
async def check_duplicate(filename: str = ""):
    if not filename:
        return {"exists": False}
    archive_path = ARCHIVE_DIR / filename
    exists = archive_path.exists()
    # Also check database
    conn = sqlite3.connect(db_path)
    db_count = conn.execute("SELECT COUNT(*) FROM quotations WHERE filename = ?", (filename,)).fetchone()[0]
    conn.close()
    return {"exists": exists, "in_database": db_count > 0, "filename": filename}

# ─── View archived PDF ────────────────────────────────────
@app.get("/archive/{filename}", dependencies=[Depends(require_role("user", "admin", "master"))])
async def serve_archive(filename: str):
    # Check archive first
    archive_path = (ARCHIVE_DIR / filename).resolve()
    # Prevent path traversal: ensure resolved path is within ARCHIVE_DIR
    if not archive_path.is_relative_to(ARCHIVE_DIR.resolve()):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    if archive_path.exists():
        return FileResponse(str(archive_path), media_type="application/pdf")
    # Fallback: check temp (for files in review phase, not yet saved)
    temp_path = (UPLOAD_DIR / filename).resolve()
    if not temp_path.is_relative_to(UPLOAD_DIR.resolve()):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    if temp_path.exists():
        return FileResponse(str(temp_path), media_type="application/pdf")
    return JSONResponse(status_code=404, content={"error": "File not found"})

# ─── Logs ─────────────────────────────────────────────────
import logging
import sys
from io import StringIO

# In-memory log buffer
log_buffer = []
MAX_LOG_LINES = 2000

class InMemoryHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        log_buffer.append(msg)
        if len(log_buffer) > MAX_LOG_LINES:
            log_buffer.pop(0)

class PrintCapture:
    """Captures print() calls to the log buffer."""
    def __init__(self, original):
        self.original = original
    def write(self, msg):
        if msg.strip():
            log_buffer.append(msg.strip())
            if len(log_buffer) > MAX_LOG_LINES:
                log_buffer.pop(0)
        self.original.write(msg)
    def flush(self):
        self.original.flush()

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
mem_handler = InMemoryHandler()
mem_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
root_logger.addHandler(mem_handler)

# Redirect print to capture it too
sys.stdout = PrintCapture(sys.stdout)
sys.stderr = PrintCapture(sys.stderr)

@app.get("/logs", dependencies=[Depends(require_role("admin", "master"))])
async def get_logs(level: str = "all"):
    lines = log_buffer if log_buffer else ["No logs captured yet. Logs are captured from app startup."]
    if level == "errors":
        lines = [l for l in lines if "error" in l.lower() or "ERROR" in l or "Traceback" in l]
    return {"logs": "\n".join(lines)}

# ─── Debug: Local PDF Parsers (master only, Phase 1+2 of v0.037.0) ──
# Read-only inspection endpoints. Run pdfplumber + PyMuPDF on
# uploaded files and return text + tables + preview URLs. Do NOT
# call the LLM, do NOT save anything, do NOT touch the existing
# processing flow. Used to validate parser quality on real PDFs
# before wiring the new pipeline into /process-all.

@app.get("/debug/files", dependencies=[Depends(require_role("master"))])
async def debug_files():
    """List all currently uploaded files (not yet archived) for the
    debug workspace file picker."""
    out = []
    for i, entry in enumerate(uploaded_files):
        filepath = entry.get("filepath", "")
        try:
            size = Path(filepath).stat().st_size if filepath else 0
        except OSError:
            size = 0
        stem = Path(entry.get("filename", "")).stem
        out.append({
            "file_index": i,
            "filename": entry.get("filename", ""),
            "file_stem": stem,
            "status": entry.get("status", "pending"),
            "num_pages": entry.get("num_pages", 0),
            "file_size": size,
            "page_urls": [f"/images/{stem}/page_{p+1}.png" for p in range(entry.get("num_pages", 0))],
        })
    return {"files": out}

@app.get("/debug/parse", dependencies=[Depends(require_role("master"))])
async def debug_parse(file_index: int):
    if file_index < 0 or file_index >= len(uploaded_files):
        raise HTTPException(status_code=404, detail="File not found")
    entry = uploaded_files[file_index]
    filepath = entry.get("filepath", "")
    if not filepath or not Path(filepath).exists():
        raise HTTPException(status_code=404, detail="File path missing or file no longer on disk")
    # Local import so the parser module is optional at startup.
    from .parser import parse_file, format_for_llm
    result = parse_file(filepath)
    # Add context the UI needs to display the result.
    result["file_index"] = file_index
    result["upload_filename"] = entry.get("filename", "")
    stem = Path(entry.get("filename", "")).stem
    result["file_stem"] = stem
    result["page_urls"] = [
        f"/images/{stem}/page_{p+1}.png"
        for p in range(result.get("num_pages", 0))
    ]
    # Phase 2 addition: a CSV-like "format for LLM" preview that the
    # LLM normalization step (Phase 3) will use. Cheap to compute, so
    # always included.
    try:
        llm_preview = format_for_llm(result)
        result["format_for_llm"] = llm_preview
    except Exception as e:
        result["format_for_llm"] = f"(format_for_llm failed: {e})"
    return result

class DebugExtractRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    file_index: int = Field(..., description="Index into uploaded_files")
    model_source: str = Field("auto", description="auto | model | part_no")
    use_llm_fallback: bool = Field(False, description="If true and local returns 0 items, call LLM")
    ocr_enabled: bool = Field(True, description="If true, run OCR on scanned PDFs (pytesseract + vision LLM fallback)")
    use_ocr_llm_fallback: bool = Field(True, description="If true and tesseract quality is low, use vision LLM")

@app.post("/debug/extract", dependencies=[Depends(require_role("master"))])
async def debug_extract(req: DebugExtractRequest):
    """Run the rules-based extractor on a previously uploaded file.

    Per-document options:
      - model_source: which column to use as the model field (auto|model|part_no)
      - use_llm_fallback: if local returns 0 items, call the LLM as a fallback

    Response shape matches extract_items() output, with an extra
    'extraction_method' field ("local" or "llm_fallback").
    """
    if req.file_index < 0 or req.file_index >= len(uploaded_files):
        raise HTTPException(status_code=404, detail="File not found")
    entry = uploaded_files[req.file_index]
    filepath = entry.get("filepath", "")
    if not filepath or not Path(filepath).exists():
        raise HTTPException(status_code=404, detail="File path missing or file no longer on disk")

    from .parser import parse_pdf_with_ocr
    from .extract import extract_items

    parse_result = await parse_pdf_with_ocr(
        filepath,
        ocr_enabled=req.ocr_enabled,
        use_llm_fallback=req.use_ocr_llm_fallback,
    )
    pp = parse_result.get("parsers", {}).get("pdfplumber", {})
    pages = pp.get("pages", [])
    full_text = "\n\n".join(p.get("text", "") for p in pages)

    # Include OCR info in response for transparency
    ocr_info = parse_result.get("parsers", {}).get("ocr", {})

    # 1) Try the rules-based extractor
    result = extract_items(parse_result, full_text, parse_result.get("filename", ""),
                           model_source=req.model_source)
    result["extraction_method"] = "local"
    result["file_index"] = req.file_index
    result["upload_filename"] = entry.get("filename", "")
    result["ocr"] = {
        "triggered": bool(ocr_info),
        "source": ocr_info.get("source", ""),
        "time_ms": ocr_info.get("time_ms", 0),
        "avg_confidence": ocr_info.get("avg_confidence"),
        "total_num_count": ocr_info.get("total_num_count"),
        "error": ocr_info.get("error"),
    }

    # 2) Fall back to LLM if requested AND local returned 0 items
    if req.use_llm_fallback and not result.get("items"):
        from .normalize import normalize_text_with_llm
        llm_result, err = await normalize_text_with_llm(full_text)
        if err:
            result["extraction_warnings"].append(f"LLM fallback failed: {err}")
        elif llm_result.get("items"):
            result["items"] = llm_result["items"]
            result["extraction_method"] = "llm_fallback"
            # Use LLM's metadata if local didn't find them
            if not result.get("supplier") and llm_result.get("supplier"):
                result["supplier"] = llm_result["supplier"]
            if not result.get("currency") and llm_result.get("currency"):
                result["currency"] = llm_result["currency"]
            if not result.get("date") and llm_result.get("date"):
                result["date"] = llm_result["date"]
            if (not result.get("document_type") or result["document_type"] == "unknown") \
                    and llm_result.get("document_type") not in (None, "", "unknown"):
                result["document_type"] = llm_result["document_type"]
            result["extraction_warnings"].append(
                f"Used LLM fallback (local returned 0 items; LLM found {len(llm_result['items'])} items)"
            )
        else:
            result["extraction_warnings"].append("LLM fallback returned 0 items")

    return result

# ─── Serve Frontend ───────────────────────────────────────
FRONTEND_PATH = Path(__file__).parent.parent / "frontend" / "index.html"

@app.get("/")
async def serve_frontend():
    return FileResponse(str(FRONTEND_PATH))

# ─── Static Files ─────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend-static")
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
