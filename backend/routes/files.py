"""
backend/routes/files.py — File upload, processing, and management endpoints.

This module handles:
    - File upload
    - PDF processing (streaming)
    - Confirmation/saving
    - Skip/delete operations
    - Export/import
"""

import json
import os
import shutil
import zipfile
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..auth import require_role
from ..db import get_db

router = APIRouter(tags=["files"])


# ─── Models ────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    file_index: int
    model_source: str = "auto"
    use_llm_fallback: bool = False
    ocr_enabled: bool = True
    use_ocr_llm_fallback: bool = True


class ConfirmRequest(BaseModel):
    file_index: int
    data: dict


class DeleteRequest(BaseModel):
    ids: list[int]


class UpdateRequest(BaseModel):
    id: int
    data: dict


# ─── Upload ────────────────────────────────────────────────

@router.post("/upload", dependencies=[Depends(require_role("admin", "master"))])
async def upload(files: list[UploadFile] = File(...)):
    """Upload PDF or XLSX files for processing."""
    from ..main import uploaded_files, UPLOAD_DIR
    
    results = []
    for file in files:
        if not file.filename:
            continue
        
        # Save file to temp directory
        filepath = UPLOAD_DIR / file.filename
        with open(filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Add to uploaded files list
        entry = {
            "filename": file.filename,
            "filepath": str(filepath),
            "status": "uploaded",
            "num_pages": 0,
            "pages": [],
        }
        uploaded_files.append(entry)
        results.append({"filename": file.filename, "status": "uploaded"})
    
    return {"uploaded": len(results), "files": results}


@router.post("/clear", dependencies=[Depends(require_role("admin", "master"))])
async def clear_files():
    """Clear all uploaded files."""
    from ..main import uploaded_files
    uploaded_files.clear()
    return {"status": "cleared"}


@router.get("/next-file", dependencies=[Depends(require_role("admin", "master"))])
async def next_file():
    """Get next file for processing."""
    from ..main import uploaded_files
    for i, entry in enumerate(uploaded_files):
        if entry["status"] == "uploaded":
            return {"file_index": i, "filename": entry["filename"]}
    return {"file_index": -1}


# ─── Process ──────────────────────────────────────────────

@router.post("/process-stream", dependencies=[Depends(require_role("admin", "master"))])
async def process_stream(req: ProcessRequest):
    """Process a file with streaming progress updates."""
    from ..main import uploaded_files
    
    if req.file_index < 0 or req.file_index >= len(uploaded_files):
        raise HTTPException(status_code=404, detail="File not found")
    
    entry = uploaded_files[req.file_index]
    filepath = entry.get("filepath", "")
    if not filepath or not Path(filepath).exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    # Process file (simplified - full implementation in original main.py)
    from ..parser import parse_file_with_ocr
    from ..extract import extract_items
    
    parse_result = await parse_file_with_ocr(filepath)
    result = extract_items(parse_result)
    
    return {
        "filename": entry["filename"],
        "items": result.get("items", []),
        "supplier": result.get("supplier", ""),
        "document_type": result.get("document_type", "unknown"),
    }


# ─── Confirm / Save ───────────────────────────────────────

@router.post("/confirm", dependencies=[Depends(require_role("admin", "master"))])
async def confirm(req: ConfirmRequest):
    """Save processed data to database."""
    from ..main import uploaded_files, ARCHIVE_DIR, IMAGES_DIR
    
    if req.file_index < 0 or req.file_index >= len(uploaded_files):
        raise HTTPException(status_code=404, detail="File not found")
    
    data = req.data
    items = data.get("items", [])
    supplier = data.get("supplier", "")
    quotation_date = items[0].get("date", "") if items else ""
    document_type = data.get("document_type", "unknown")
    
    # Insert into database
    with get_db() as db:
        db.execute(
            "INSERT INTO quotations (filename, supplier, quotation_date, items, document_type) VALUES (?, ?, ?, ?, ?)",
            (uploaded_files[req.file_index]["filename"], supplier, quotation_date,
             json.dumps(items), document_type)
        )
        last_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    
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
    return {"status": "saved", "id": last_id}


@router.post("/skip", dependencies=[Depends(require_role("admin", "master"))])
async def skip(req: ProcessRequest):
    """Skip current file."""
    from ..main import uploaded_files
    uploaded_files[req.file_index]["status"] = "skipped"
    return {"status": "skipped"}


# ─── Delete / Update ──────────────────────────────────────

@router.post("/delete", dependencies=[Depends(require_role("admin", "master"))])
async def delete(req: DeleteRequest):
    """Delete quotations by ID."""
    if not req.ids:
        return {"status": "nothing to delete"}
    
    # Get filenames before deleting
    placeholders = ",".join("?" * len(req.ids))
    with get_db(readonly=True) as db:
        rows = db.execute(
            f"SELECT filename FROM quotations WHERE id IN ({placeholders})", req.ids
        ).fetchall()
    
    # Delete from database
    with get_db() as db:
        db.execute(f"DELETE FROM quotations WHERE id IN ({placeholders})", req.ids)
    
    # Delete archived PDFs
    from ..main import ARCHIVE_DIR
    for row in rows:
        archive_path = ARCHIVE_DIR / row[0]
        try:
            if archive_path.exists():
                archive_path.unlink()
        except OSError:
            pass
    
    return {"status": "deleted", "count": len(req.ids)}


@router.post("/update", dependencies=[Depends(require_role("admin", "master"))])
async def update(req: UpdateRequest):
    """Update a quotation."""
    data = req.data
    items = data.get("items", [])
    supplier = data.get("supplier", "")
    quotation_date = items[0].get("date", "") if items else ""
    document_type = data.get("document_type", "unknown")
    
    with get_db() as db:
        cur = db.execute(
            "UPDATE quotations SET supplier=?, quotation_date=?, items=?, document_type=? WHERE id=?",
            (supplier, quotation_date, json.dumps(items), document_type, req.id)
        )
    
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return {"status": "updated"}


# ─── Export / Import ──────────────────────────────────────

@router.get("/export", dependencies=[Depends(require_role("admin", "master"))])
async def export_db():
    """Export all quotations as a zip file."""
    from ..main import ARCHIVE_DIR
    
    with get_db(readonly=True) as db:
        rows = db.execute("SELECT * FROM quotations ORDER BY created_at DESC").fetchall()
    
    data = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("items"), str):
            try:
                d["items"] = json.loads(d["items"])
            except (json.JSONDecodeError, TypeError):
                d["items"] = []
        data.append(d)
    
    # Create zip file
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
            os.unlink(zip_path)
        
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


@router.post("/import/upload", dependencies=[Depends(require_role("admin", "master"))])
async def import_upload(file: UploadFile = File(...)):
    """Import quotations from a JSON or ZIP file."""
    content = await file.read()
    quotations = []
    pdf_restored = 0
    
    if file.filename.endswith(".zip"):
        import io
        try:
            with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
                if "quotations.json" in zf.namelist():
                    data = json.loads(zf.read("quotations.json"))
                    quotations = data.get("quotations", [])
                from ..main import ARCHIVE_DIR
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
            return JSONResponse(status_code=400, content={"error": "Invalid zip file"})
    elif file.filename.endswith(".json"):
        try:
            data = json.loads(content)
            quotations = data.get("quotations", [])
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    else:
        return JSONResponse(status_code=400, content={"error": "Use .zip or .json"})
    
    if not quotations:
        return JSONResponse(status_code=400, content={"error": "No quotations found"})
    
    imported = 0
    with get_db() as db:
        for q in quotations:
            supplier = q.get("supplier", "")
            quotation_date = q.get("quotation_date", "")
            document_type = q.get("document_type", "unknown")
            items = q.get("items", [])
            if isinstance(items, str):
                try:
                    items = json.loads(items)
                except (json.JSONDecodeError, TypeError):
                    items = []
            if not quotation_date and items:
                quotation_date = items[0].get("date", "")
            db.execute(
                "INSERT INTO quotations (filename, supplier, quotation_date, items, document_type) VALUES (?, ?, ?, ?, ?)",
                (q.get("filename", "imported.pdf"), supplier, quotation_date,
                 json.dumps(items), document_type)
            )
            imported += 1
    
    return {"status": "imported", "count": imported, "pdfs_restored": pdf_restored}
