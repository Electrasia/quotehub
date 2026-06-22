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
import logging
import re
from datetime import datetime
from pathlib import Path
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
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


def _validate_config(config: dict) -> list[str]:
    """Validate config values. Returns list of error messages (empty = valid)."""
    errors = []

    # timeout: 10-300 seconds
    timeout = config.get("timeout")
    if timeout is not None:
        if not isinstance(timeout, (int, float)) or timeout < 10 or timeout > 300:
            errors.append("timeout must be between 10 and 300 seconds")

    # max_retries: 1-10
    retries = config.get("max_retries")
    if retries is not None:
        if not isinstance(retries, (int, float)) or retries < 1 or retries > 10:
            errors.append("max_retries must be between 1 and 10")

    # popup_duration: 1-10 seconds
    popup = config.get("popup_duration")
    if popup is not None:
        if not isinstance(popup, (int, float)) or popup < 1 or popup > 10:
            errors.append("popup_duration must be between 1 and 10 seconds")

    # extraction_enabled: must be boolean
    enabled = config.get("extraction_enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("extraction_enabled must be true or false")

    # ai_endpoint: must be empty or a valid URL
    endpoint = config.get("ai_endpoint")
    if endpoint is not None and endpoint != "":
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            errors.append("ai_endpoint must be a URL starting with http:// or https://")

    # max_upload_size_mb: integer 1-20
    max_size = config.get("max_upload_size_mb")
    if max_size is not None:
        if not isinstance(max_size, int) or isinstance(max_size, bool) or max_size < 1 or max_size > 20:
            errors.append("max_upload_size_mb must be an integer between 1 and 20")

    # ocr_enabled, ocr_fallback_to_llm: must be boolean
    for key in ("ocr_enabled", "ocr_fallback_to_llm"):
        val = config.get(key)
        if val is not None and not isinstance(val, bool):
            errors.append(f"{key} must be true or false")

    return errors


@router.post("/config", dependencies=[Depends(require_role("master"))])
async def update_config(config: dict):
    """Update configuration."""
    errors = _validate_config(config)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
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


def _get_old_quotations(db, cutoff_date: str, document_type: str = "all") -> list:
    """Return list of (id, filename) for quotations older than cutoff_date.
    
    Filters on quotation_date (the date on the document), not created_at (insertion time).
    Skips rows with NULL or empty quotation_date.
    Optionally filters by document_type (PO, QUO, PL). "all" or "ALL" returns all types.
    """
    base_query = "SELECT id, filename FROM quotations WHERE quotation_date IS NOT NULL AND quotation_date != '' AND date(quotation_date) < ?"
    params = [cutoff_date]
    
    if document_type and document_type.upper() != "ALL":
        base_query += " AND document_type = ?"
        params.append(document_type.upper())
    
    rows = db.execute(base_query, params).fetchall()
    return [(r["id"], r["filename"]) for r in rows]


class CleanupPreviewRequest(BaseModel):
    months: int = Field(3, ge=1, le=60)
    document_type: str = Field("all", pattern="^(all|ALL|PO|QUO|PL)$")


class CleanupExecuteRequest(BaseModel):
    months: int = Field(3, ge=1, le=60)
    delete_files: bool = False
    document_type: str = Field("all", pattern="^(all|ALL|PO|QUO|PL)$")


@router.post("/cleanup/preview", dependencies=[Depends(require_role("master"))])
async def cleanup_preview(req: CleanupPreviewRequest):
    """Preview what would be deleted."""
    cutoff_date = _cleanup_cutoff(req.months)
    
    from ..main import ARCHIVE_DIR
    with get_db(readonly=True) as db:
        rows = _get_old_quotations(db, cutoff_date, req.document_type)
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
    import sqlite3
    cutoff_date = _cleanup_cutoff(req.months)
    
    from ..main import ARCHIVE_DIR, IMAGES_DIR
    from ..db import DB_PATH
    
    # Get old quotations
    with get_db(readonly=True) as db:
        rows = _get_old_quotations(db, cutoff_date, req.document_type)
        seen = set()
        targets = []
        for _id, filename in rows:
            if filename and filename not in seen:
                seen.add(filename)
                pdf_path = ARCHIVE_DIR / filename
                img_dir = IMAGES_DIR / Path(filename).stem
                targets.append((filename, pdf_path, img_dir))
    
    # Delete from database with error handling
    try:
        with get_db() as db:
            base_query = "DELETE FROM quotations WHERE quotation_date IS NOT NULL AND quotation_date != '' AND date(quotation_date) < ?"
            params = [cutoff_date]
            
            if req.document_type and req.document_type.upper() != "ALL":
                base_query += " AND document_type = ?"
                params.append(req.document_type.upper())
            
            cur = db.execute(base_query, params)
            entries_deleted = cur.rowcount
    except Exception as e:
        logger.exception("Cleanup DB operation failed")
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": f"Database error: {str(e)}"}
        )
    
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
    try:
        vacuum_conn = sqlite3.connect(DB_PATH)
        vacuum_conn.execute("VACUUM")
        vacuum_conn.close()
    except Exception:
        logger.warning("VACUUM failed during cleanup", exc_info=True)
        pass
    
    return {
        "success": True,
        "entries_deleted": entries_deleted,
        "files_deleted": file_count_deleted,
        "bytes_freed": bytes_freed,
    }


@router.get("/cleanup/stats", dependencies=[Depends(require_role("master"))])
async def cleanup_stats():
    """Get current database and file statistics for cleanup overview."""
    from ..main import ARCHIVE_DIR, IMAGES_DIR, UPLOAD_DIR, uploaded_files, uploaded_files_lock
    
    stats = {}
    
    # ── Database stats ──
    with get_db(readonly=True) as db:
        # Total entries
        row = db.execute("SELECT COUNT(*) as total FROM quotations").fetchone()
        stats["total_entries"] = row["total"]
        
        # Entries by document type
        rows = db.execute(
            "SELECT document_type, COUNT(*) as count FROM quotations GROUP BY document_type"
        ).fetchall()
        stats["by_type"] = {r["document_type"]: r["count"] for r in rows}
        
        # Oldest and newest dates
        row = db.execute(
            "SELECT MIN(quotation_date) as oldest, MAX(quotation_date) as newest FROM quotations WHERE quotation_date IS NOT NULL AND quotation_date != ''"
        ).fetchone()
        stats["oldest_date"] = row["oldest"]
        stats["newest_date"] = row["newest"]
        
        # DB stems for orphan detection
        db_stems = set()
        for r in db.execute("SELECT filename FROM quotations WHERE filename IS NOT NULL AND filename != ''").fetchall():
            db_stems.add(Path(r["filename"]).stem)
    
    # ── Archive file stats ──
    pdf_count = 0
    total_size = 0
    archive_stems = set()
    if ARCHIVE_DIR.exists():
        for f in ARCHIVE_DIR.iterdir():
            if f.is_file():
                pdf_count += 1
                archive_stems.add(f.stem)
                try:
                    total_size += f.stat().st_size
                except OSError:
                    pass
    
    # ── Queue reference set ──
    async with uploaded_files_lock:
        queue_filenames = {e["filename"] for e in uploaded_files if e.get("filename")}
    queue_stems = {Path(fname).stem for fname in queue_filenames}
    
    # ── Active stems (anything referenced by queue, archive, or DB) ──
    all_active_stems = queue_stems | archive_stems | db_stems
    
    # ── Image dir stats ──
    img_count = 0
    image_orphan_count = 0
    image_orphan_bytes = 0
    if IMAGES_DIR.exists():
        for item in IMAGES_DIR.iterdir():
            if item.is_dir():
                img_count += 1
                if item.name not in all_active_stems:
                    image_orphan_count += 1
                    for p in item.rglob("*"):
                        if p.is_file():
                            try:
                                image_orphan_bytes += p.stat().st_size
                            except OSError:
                                pass
    
    # ── Temp file stats ──
    temp_file_count = 0
    temp_orphan_count = 0
    temp_orphan_bytes = 0
    if UPLOAD_DIR.exists():
        for f in UPLOAD_DIR.iterdir():
            if f.is_file():
                temp_file_count += 1
                if f.name not in queue_filenames:
                    temp_orphan_count += 1
                    try:
                        temp_orphan_bytes += f.stat().st_size
                    except OSError:
                        pass
    
    stats["pdf_files"] = pdf_count
    stats["image_dirs"] = img_count
    stats["total_size"] = total_size
    stats["temp_file_count"] = temp_file_count
    stats["temp_orphan_count"] = temp_orphan_count
    stats["image_orphan_count"] = image_orphan_count
    stats["temp_orphan_bytes"] = temp_orphan_bytes
    stats["image_orphan_bytes"] = image_orphan_bytes
    
    return stats


@router.post("/cleanup/purge-orphans", dependencies=[Depends(require_role("master"))])
async def cleanup_purge_orphans():
    """Delete orphan files with no reference in queue, archive, or database.
    
    Orphan temp files exist in data/temp/ but have no matching queue entry.
    Orphan image directories exist in data/images/ whose stem matches no
    file in the queue, archive, or database.
    
    Returns counts and bytes freed.
    """
    import shutil
    from ..main import uploaded_files, uploaded_files_lock, UPLOAD_DIR, ARCHIVE_DIR, IMAGES_DIR
    
    # ── Build reference sets ──
    async with uploaded_files_lock:
        queue_filenames = {e["filename"] for e in uploaded_files if e.get("filename")}
    queue_stems = {Path(fname).stem for fname in queue_filenames}
    
    archive_stems = set()
    if ARCHIVE_DIR.exists():
        for f in ARCHIVE_DIR.iterdir():
            if f.is_file():
                archive_stems.add(f.stem)
    
    db_stems = set()
    with get_db(readonly=True) as db:
        for row in db.execute("SELECT filename FROM quotations WHERE filename IS NOT NULL AND filename != ''").fetchall():
            db_stems.add(Path(row["filename"]).stem)
    
    all_active_stems = queue_stems | archive_stems | db_stems
    
    temp_deleted = 0
    image_dirs_deleted = 0
    bytes_freed = 0
    
    # ── Delete orphan temp files ──
    if UPLOAD_DIR.exists():
        for f in UPLOAD_DIR.iterdir():
            if f.is_file() and f.name not in queue_filenames:
                try:
                    bytes_freed += f.stat().st_size
                    f.unlink()
                    temp_deleted += 1
                except OSError:
                    pass
    
    # ── Delete orphan image directories ──
    if IMAGES_DIR.exists():
        for item in IMAGES_DIR.iterdir():
            if item.is_dir() and item.name not in all_active_stems:
                try:
                    for p in item.rglob("*"):
                        if p.is_file():
                            try:
                                bytes_freed += p.stat().st_size
                            except OSError:
                                pass
                    shutil.rmtree(item, ignore_errors=True)
                    image_dirs_deleted += 1
                except OSError:
                    pass
    
    logger.info("Orphan files purged", extra={
        'category': 'ADMIN',
        'temp_deleted': temp_deleted,
        'image_dirs_deleted': image_dirs_deleted,
        'bytes_freed': bytes_freed
    })
    
    return {
        "temp_deleted": temp_deleted,
        "image_dirs_deleted": image_dirs_deleted,
        "bytes_freed": bytes_freed,
    }


# ─── Search ────────────────────────────────────────────────

@router.get("/search", dependencies=[Depends(require_role("user", "admin", "master"))])
async def search(q: str = "", document_type: str = ""):
    """Search quotations using full-text search with optional document type filter."""
    import sqlite3
    
    with get_db(readonly=True) as db:
        if q:
            words = q.strip().split()
            sanitized = [re.sub(r'[^\w]', '', w) for w in words if re.sub(r'[^\w]', '', w)]
            fts_words = [w.replace('"', '""') + '*' for w in sanitized]
            fts_query = " ".join(fts_words)
            
            # Build base query with optional document_type filter
            doc_type_filter = ""
            params = [fts_query]
            if document_type and document_type.upper() != "ALL":
                doc_type_filter = " AND q.document_type = ?"
                params.append(document_type.upper())
            
            try:
                rows = db.execute(
                    f"""SELECT q.* FROM quotations q
                       INNER JOIN quotations_fts fts ON q.id = fts.rowid
                       WHERE quotations_fts MATCH ?{doc_type_filter}
                       ORDER BY q.created_at DESC""",
                    params
                ).fetchall()
            except sqlite3.OperationalError:
                # Fallback to LIKE search
                if document_type and document_type.upper() != "ALL":
                    if len(words) == 1:
                        rows = db.execute(
                            "SELECT * FROM quotations WHERE (supplier LIKE ? OR items LIKE ?) AND document_type = ? ORDER BY created_at DESC",
                            (f"%{words[0]}%", f"%{words[0]}%", document_type.upper())
                        ).fetchall()
                    else:
                        conditions = []
                        params = []
                        for word in words:
                            conditions.append("(supplier LIKE ? OR items LIKE ?)")
                            params.extend([f"%{word}%", f"%{word}%"])
                        where_clause = " AND ".join(conditions)
                        params.append(document_type.upper())
                        rows = db.execute(
                            f"SELECT * FROM quotations WHERE {where_clause} AND document_type = ? ORDER BY created_at DESC",
                            params
                        ).fetchall()
                else:
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
            # No search query — just filter by document_type if provided
            if document_type and document_type.upper() != "ALL":
                rows = db.execute(
                    "SELECT * FROM quotations WHERE document_type = ? ORDER BY created_at DESC LIMIT 10",
                    (document_type.upper(),)
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM quotations ORDER BY created_at DESC LIMIT 10").fetchall()
    
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
                    str(d.get("supplier", "")),
                    str(item.get("brand", "")),
                    str(item.get("model", "")),
                    str(item.get("description", "")),
                    str(item.get("unit_price", "")),
                ]).lower()
                if all(w in searchable for w in words):
                    filtered.append(item)
            d["items"] = filtered
        results.append(d)
    
    # Determine if results are limited (empty search with no query)
    limited = (not q or not q.strip()) and len(rows) == 10
    
    return {"results": results, "limited": limited}


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
    """Check if a file is already in the database."""
    if not filename:
        return {"in_database": False}
    
    with get_db(readonly=True) as db:
        db_count = db.execute(
            "SELECT COUNT(*) FROM quotations WHERE filename = ?", (filename,)
        ).fetchone()[0]
    
    return {"in_database": db_count > 0, "filename": filename}


# ─── View archived file ──────────────────────────────────

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".txt": "text/plain",
}

@router.get("/archive/{filename}", dependencies=[Depends(require_role("user", "admin", "master"))])
async def serve_archive(filename: str):
    """Serve archived file (PDF, XLSX, etc.)."""
    from ..main import ARCHIVE_DIR, UPLOAD_DIR
    
    suffix = Path(filename).suffix.lower()
    media_type = MIME_TYPES.get(suffix, "application/octet-stream")
    
    archive_path = (ARCHIVE_DIR / filename).resolve()
    if not archive_path.is_relative_to(ARCHIVE_DIR.resolve()):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    if archive_path.exists():
        return FileResponse(str(archive_path), media_type=media_type)
    
    temp_path = (UPLOAD_DIR / filename).resolve()
    if not temp_path.is_relative_to(UPLOAD_DIR.resolve()):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    if temp_path.exists():
        return FileResponse(str(temp_path), media_type=media_type)
    
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
