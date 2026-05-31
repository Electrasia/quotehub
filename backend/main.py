import os
import json
import base64
import sqlite3
import shutil
import re
import zipfile
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
from pdf2image import convert_from_path
from PIL import Image

# ─── Config ───────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"ai_endpoint": "", "model": "", "timeout": 30, "max_retries": 5}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def get_config_data():
    cfg = load_config()
    cfg.setdefault("external_url", "")
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
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
    conn.commit()
    conn.close()

init_db()

# ─── App ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    print(f"QuoDB starting. AI endpoint: {cfg.get('ai_endpoint', 'NOT SET')}")
    print(f"QuoDB starting. AI model: {cfg.get('model', 'NOT SET')}")
    print(f"QuoDB starting. AI connected: {ai_connected}")
    load_upload_state()
    yield

app = FastAPI(lifespan=lifespan)

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

# ─── Config Endpoints ─────────────────────────────────────
@app.get("/config")
async def get_config_route():
    return get_config_data()

@app.post("/config")
async def update_config(req: ConfigRequest):
    cfg = get_config_data()
    cfg["ai_endpoint"] = req.ai_endpoint
    cfg["model"] = req.model
    cfg["external_url"] = req.external_url
    cfg["timeout"] = req.timeout
    cfg["max_retries"] = req.max_retries
    cfg["popup_duration"] = req.popup_duration
    save_config(cfg)
    return {"status": "saved", "config": cfg}

# ─── AI Connection ────────────────────────────────────────
@app.post("/ai/connect")
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

@app.get("/ai/status")
async def ai_status():
    return {"connected": ai_connected}

# ─── Upload ───────────────────────────────────────────────
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        return JSONResponse(status_code=400, content={"error": "Only PDF files allowed"})

    filepath = UPLOAD_DIR / file.filename
    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    # Convert PDF pages to images
    page_images = []
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

@app.post("/clear")
async def clear_files():
    global uploaded_files
    uploaded_files = []
    save_upload_state()
    return {"status": "cleared"}

# ─── Next File (PDF preview) ──────────────────────────────
@app.get("/next-file")
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

    prompt = f"""Analyze this supplier quotation image. Extract ALL information and return ONLY valid JSON.

Return this exact structure:
{{
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

CRITICAL RULES:
- description: Copy EVERY word from the description column verbatim. If it contains inch marks like 10.1", replace the " with a single quote ' like 10.1'. Do NOT skip any text.
- unit_price: Format as X,XXX.XX — always use comma as thousands separator and period as decimal separator. Always show exactly 2 decimal places. Remove ALL currency symbols ($, €, £, HK$, MOP$, etc.). Examples: 2000 → 2,000.00, 12345.6 → 12,345.60, 99.9 → 99.90
- date: ALWAYS convert to YYYY-MM-DD format. Example: 20/1/2026 becomes 2026-01-20, January 20 2026 becomes 2026-01-20.
- currency: $ alone = USD, HK$ = HKD, € = EUR, £ = GBP, MOP$ = MOP
- Return ONLY valid parseable JSON, no markdown, no explanation"""

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

@app.post("/process-all")
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

    if not all_items and errors:
        return JSONResponse(status_code=500, content={"status": "error", "error": "; ".join(errors)})

    merged = {
        "supplier": supplier,
        "items": all_items
    }

    uploaded_files[req.file_index]["status"] = "processed"
    save_upload_state()
    return {"status": "success", "data": merged, "pages_processed": file_entry["num_pages"], "errors": errors}

@app.post("/process-stream")
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
        shared_date = ""
        errors = []

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

        merged = {"supplier": supplier, "items": all_items}
        uploaded_files[req.file_index]["status"] = "processed"
        save_upload_state()

        # Send final result
        yield f"data: {json.dumps({'type': 'done', 'data': merged, 'pages_processed': total_pages, 'errors': errors})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ─── Confirm / Save ───────────────────────────────────────
@app.post("/confirm")
async def confirm(req: ConfirmRequest):
    data = req.data
    items = data.get("items", [])
    supplier = data.get("supplier", "") or (items[0].get("supplier", "") if items else "")
    quotation_date = items[0].get("date", "") if items else ""

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO quotations (filename, supplier, quotation_date, items) VALUES (?, ?, ?, ?)",
        (uploaded_files[req.file_index]["filename"],
         supplier,
         quotation_date,
         json.dumps(items))
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
@app.post("/skip")
async def skip(req: ProcessRequest):
    uploaded_files[req.file_index]["status"] = "skipped"
    save_upload_state()
    return {"status": "skipped"}

class DeleteRequest(BaseModel):
    ids: list[int]

class UpdateRequest(BaseModel):
    id: int
    data: dict

@app.post("/delete")
async def delete(req: DeleteRequest):
    if not req.ids:
        return {"status": "nothing to delete"}
    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(req.ids))
    rows = conn.execute(f"SELECT filename FROM quotations WHERE id IN ({placeholders})", req.ids).fetchall()
    for row in rows:
        archive_path = ARCHIVE_DIR / row[0]
        if archive_path.exists():
            archive_path.unlink()
    conn.execute(f"DELETE FROM quotations WHERE id IN ({placeholders})", req.ids)
    conn.commit()
    conn.close()
    return {"status": "deleted", "count": len(req.ids)}

@app.post("/update")
async def update_quotation(req: UpdateRequest):
    data = req.data
    items = data.get("items", [])
    supplier = data.get("supplier", "")
    quotation_date = items[0].get("date", "") if items else ""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE quotations SET supplier=?, quotation_date=?, items=? WHERE id=?",
        (supplier, quotation_date, json.dumps(items), req.id)
    )
    conn.commit()
    conn.close()
    return {"status": "updated"}

# ─── Export / Import ──────────────────────────────────────
@app.get("/export")
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

@app.post("/import/upload")
async def import_upload(file: UploadFile = File(...)):
    """Import quotations from a JSON or ZIP file."""
    content = await file.read()

    quotations = []
    pdf_restored = 0

    if file.filename.endswith(".zip"):
        # Import from zip
        import io
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            # Read quotations.json
            if "quotations.json" in zf.namelist():
                data = json.loads(zf.read("quotations.json"))
                quotations = data.get("quotations", [])
            # Restore PDFs
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            for name in zf.namelist():
                if name.startswith("archive/") and name.endswith(".pdf"):
                    pdf_name = name.split("/", 1)[1]
                    pdf_data = zf.read(name)
                    pdf_path = ARCHIVE_DIR / pdf_name
                    with open(pdf_path, "wb") as f:
                        f.write(pdf_data)
                    pdf_restored += 1
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
        items = q.get("items", [])
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except (json.JSONDecodeError, TypeError):
                items = []
        if not quotation_date and items:
            quotation_date = items[0].get("date", "")
        conn.execute(
            "INSERT INTO quotations (filename, supplier, quotation_date, items) VALUES (?, ?, ?, ?)",
            (q.get("filename", "imported.pdf"), supplier, quotation_date, json.dumps(items))
        )
        imported += 1
    conn.commit()
    conn.close()
    return {"status": "imported", "count": imported, "pdfs_restored": pdf_restored}

# ─── Search ───────────────────────────────────────────────
@app.get("/search")
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

# ─── Duplicate Check ──────────────────────────────────────
@app.get("/check-duplicate")
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
@app.get("/archive/{filename}")
async def serve_archive(filename: str):
    filepath = (ARCHIVE_DIR / filename).resolve()
    # Prevent path traversal: ensure resolved path is within ARCHIVE_DIR
    if not filepath.is_relative_to(ARCHIVE_DIR.resolve()):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    if filepath.exists():
        return FileResponse(str(filepath), media_type="application/pdf")
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

@app.get("/logs")
async def get_logs(level: str = "all"):
    lines = log_buffer if log_buffer else ["No logs captured yet. Logs are captured from app startup."]
    if level == "errors":
        lines = [l for l in lines if "error" in l.lower() or "ERROR" in l or "Traceback" in l]
    return {"logs": "\n".join(lines)}

# ─── Serve Frontend ───────────────────────────────────────
FRONTEND_PATH = Path(__file__).parent.parent / "frontend" / "index.html"

@app.get("/")
async def serve_frontend():
    return FileResponse(str(FRONTEND_PATH))

# ─── Static Files ─────────────────────────────────────────
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
