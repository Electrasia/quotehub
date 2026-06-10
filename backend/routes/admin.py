"""
backend/routes/admin.py — Configuration and system administration endpoints.

This module handles:
    - Configuration management
    - System cleanup
    - Search
    - Brand suggestions
    - Duplicate checking
    - PDF viewing
    - Logs
"""

import calendar
import json
from datetime import datetime
from pathlib import Path
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..auth import require_role
from ..db import get_db
from ..utils import load_config, save_config

router = APIRouter(tags=["admin"])


# ─── Config ────────────────────────────────────────────────

@router.get("/config", dependencies=[Depends(require_role("admin", "master"))])
async def get_config():
    """Get current configuration."""
    return load_config()


@router.post("/config", dependencies=[Depends(require_role("master"))])
async def update_config(config: dict):
    """Update configuration."""
    save_config(config)
    return {"status": "saved"}


@router.get("/version")
async def get_version():
    """Get app version."""
    from ..main import APP_VERSION, APP_COMMIT
    return {"version": APP_VERSION, "commit": APP_COMMIT}


# ─── System Cleanup ────────────────────────────────────────

def _cleanup_cutoff(months: int) -> str:
    """Return ISO date (YYYY-MM-DD) for 'X months ago'."""
    now = datetime.now()
    year = now.year
    month = now.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day).date().isoformat()


def _get_old_quotations(db, cutoff_date: str) -> list:
    """Return list of (id, filename) for quotations older than cutoff_date.
    
    Filters on quotation_date (the date on the document), not created_at (insertion time).
    Skips rows with NULL or empty quotation_date.
    """
    rows = db.execute(
        "SELECT id, filename FROM quotations WHERE quotation_date IS NOT NULL AND quotation_date != '' AND date(quotation_date) < ?",
        (cutoff_date,)
    ).fetchall()
    return [(r["id"], r["filename"]) for r in rows]


class CleanupPreviewRequest(BaseModel):
    months: int = Field(3, ge=1, le=60)


class CleanupExecuteRequest(BaseModel):
    months: int = Field(3, ge=1, le=60)
    delete_files: bool = False


@router.post("/cleanup/preview", dependencies=[Depends(require_role("master"))])
async def cleanup_preview(req: CleanupPreviewRequest):
    """Preview what would be deleted."""
    cutoff_date = _cleanup_cutoff(req.months)
    
    from ..main import ARCHIVE_DIR
    with get_db(readonly=True) as db:
        rows = _get_old_quotations(db, cutoff_date)
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


@router.post("/cleanup/execute", dependencies=[Depends(require_role("master"))])
async def cleanup_execute(req: CleanupExecuteRequest):
    """Delete old quotations."""
    import shutil
    cutoff_date = _cleanup_cutoff(req.months)
    
    from ..main import ARCHIVE_DIR, IMAGES_DIR, DB_PATH
    
    # Get old quotations
    with get_db(readonly=True) as db:
        rows = _get_old_quotations(db, cutoff_date)
        seen = set()
        targets = []
        for _id, filename in rows:
            if filename and filename not in seen:
                seen.add(filename)
                pdf_path = ARCHIVE_DIR / filename
                img_dir = IMAGES_DIR / Path(filename).stem
                targets.append((filename, pdf_path, img_dir))
    
    # Delete from database
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM quotations WHERE quotation_date IS NOT NULL AND quotation_date != '' AND date(quotation_date) < ?",
            (cutoff_date,)
        )
        entries_deleted = cur.rowcount
    
    # Delete files
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
                pass
            try:
                if img_dir.exists():
                    shutil.rmtree(img_dir, ignore_errors=True)
            except OSError:
                pass
    
    # VACUUM
    import sqlite3
    try:
        vacuum_conn = sqlite3.connect(DB_PATH)
        vacuum_conn.execute("VACUUM")
        vacuum_conn.close()
    except Exception:
        pass
    
    return {
        "entries_deleted": entries_deleted,
        "files_deleted": file_count_deleted,
        "bytes_freed": bytes_freed,
    }


# ─── Search ────────────────────────────────────────────────

@router.get("/search", dependencies=[Depends(require_role("user", "admin", "master"))])
async def search(q: str = ""):
    """Search quotations using full-text search."""
    import sqlite3
    
    with get_db(readonly=True) as db:
        if q:
            words = q.strip().split()
            fts_words = [w.replace('"', '""') + '*' for w in words]
            fts_query = " ".join(fts_words)
            
            try:
                rows = db.execute(
                    """SELECT q.* FROM quotations q
                       INNER JOIN quotations_fts fts ON q.id = fts.rowid
                       WHERE quotations_fts MATCH ?
                       ORDER BY q.created_at DESC""",
                    (fts_query,)
                ).fetchall()
            except sqlite3.OperationalError:
                if len(words) == 1:
                    rows = db.execute(
                        "SELECT * FROM quotations WHERE supplier LIKE ? OR items LIKE ? ORDER BY created_at DESC",
                        (f"%{words[0]}%", f"%{words[0]}%")
                    ).fetchall()
                else:
                    conditions = []
                    params = []
                    for word in words:
                        conditions.append("(supplier LIKE ? OR items LIKE ?)")
                        params.extend([f"%{word}%", f"%{word}%"])
                    where_clause = " AND ".join(conditions)
                    rows = db.execute(
                        f"SELECT * FROM quotations WHERE {where_clause} ORDER BY created_at DESC",
                        params
                    ).fetchall()
        else:
            rows = db.execute("SELECT * FROM quotations ORDER BY created_at DESC").fetchall()
    
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
        if words and d.get("items"):
            filtered = []
            for item in d["items"]:
                searchable = " ".join([
                    str(item.get("brand", "")),
                    str(item.get("model", "")),
                    str(item.get("description", "")),
                    str(item.get("unit_price", "")),
                ]).lower()
                if all(w in searchable for w in words):
                    filtered.append(item)
            d["items"] = filtered
        results.append(d)
    return results


# ─── Brand Suggestion ─────────────────────────────────────

@router.get("/items/by-model", dependencies=[Depends(require_role("user", "admin", "master"))])
async def get_brand_by_model(model: str = ""):
    """Look up the most common brand for a given model."""
    if not model:
        return {"model": model, "brand": None, "count": 0}
    
    with get_db(readonly=True) as db:
        rows = db.execute("SELECT items FROM quotations").fetchall()
    
    model_lc = model.strip().lower()
    normalized_groups = {}
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
    
    most_common_key = max(normalized_groups, key=lambda k: len(normalized_groups[k]))
    candidates = normalized_groups[most_common_key]
    most_common_brand = Counter(candidates).most_common(1)[0][0]
    return {"model": model, "brand": most_common_brand, "count": len(candidates)}


# ─── Duplicate Check ──────────────────────────────────────

@router.get("/check-duplicate", dependencies=[Depends(require_role("user", "admin", "master"))])
async def check_duplicate(filename: str = ""):
    """Check if a file already exists."""
    if not filename:
        return {"exists": False}
    
    from ..main import ARCHIVE_DIR
    archive_path = ARCHIVE_DIR / filename
    exists = archive_path.exists()
    
    with get_db(readonly=True) as db:
        db_count = db.execute(
            "SELECT COUNT(*) FROM quotations WHERE filename = ?", (filename,)
        ).fetchone()[0]
    
    return {"exists": exists, "in_database": db_count > 0, "filename": filename}


# ─── View archived PDF ────────────────────────────────────

@router.get("/archive/{filename}", dependencies=[Depends(require_role("user", "admin", "master"))])
async def serve_archive(filename: str):
    """Serve archived PDF file."""
    from ..main import ARCHIVE_DIR, UPLOAD_DIR
    
    archive_path = (ARCHIVE_DIR / filename).resolve()
    if not archive_path.is_relative_to(ARCHIVE_DIR.resolve()):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    if archive_path.exists():
        return FileResponse(str(archive_path), media_type="application/pdf")
    
    temp_path = (UPLOAD_DIR / filename).resolve()
    if not temp_path.is_relative_to(UPLOAD_DIR.resolve()):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    if temp_path.exists():
        return FileResponse(str(temp_path), media_type="application/pdf")
    
    return JSONResponse(status_code=404, content={"error": "File not found"})


# ─── Logs ─────────────────────────────────────────────────

@router.get("/logs", dependencies=[Depends(require_role("admin", "master"))])
async def get_logs(level: str = "all", category: str = "all"):
    """Get application logs with optional filtering.
    
    Args:
        level: Filter by log level - 'all' or 'errors'
        category: Filter by category - 'all', 'auth', 'process', 'ai', 'admin'
    """
    from ..main import log_buffer
    
    logs = list(log_buffer)[-500:]  # Last 500 lines
    
    # Filter by level
    if level == "errors":
        logs = [line for line in logs if "[ERROR]" in line]
    
    # Filter by category
    if category != "all":
        category_tag = f"[{category.upper()}]"
        logs = [line for line in logs if category_tag in line]
    
    return {"logs": logs}
