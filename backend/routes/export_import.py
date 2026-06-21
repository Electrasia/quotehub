"""
backend/routes/export_import.py — Encrypted export/import endpoints.

Provides:
    - Encrypted .quodb export download
    - Encrypted .quodb import with dry-run support

Exports use a per-file password supplied by the user at export time
(not stored). The same password must be provided at import time.

The password belongs to the FILE, not the system. There is no
"forgot password" recovery. If the password is lost, the backup
is permanently unrecoverable.

Import supports:
    - Dry-run mode (analyze without applying)
    - systemId mismatch override (force_system_id flag)
    - Duplicate detection via deterministic record hash
    - File conflict detection (existing file with different SHA-256)
    - Transactional apply with automatic rollback on error
"""

import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ..auth import get_current_user, require_role
from ..export_import import (
    run_export,
    run_import,
)

router = APIRouter(tags=["export-import"])


# ─── Models ────────────────────────────────────────────────


class ExportRunRequest(BaseModel):
    """Trigger an encrypted export."""
    password: str


# ─── Export ───────────────────────────────────────────────


@router.post("/export/run", dependencies=[Depends(require_role("master"))])
async def run_export_endpoint(req: ExportRunRequest, request: Request):
    """Run encrypted export and download the .quodb package.

    The password is used once to encrypt the file and is NOT stored.
    The user must remember it for later import.

    The export workflow:
    1. Runs PRAGMA integrity_check on the database
    2. Reads all records and verifies referenced files exist in archive
    3. Reports orphan files on disk (warnings, not blocking)
    4. Snapshots the DB via sqlite3.backup()
    5. Copies files with streaming SHA-256
    6. Builds a ZIP64 archive with manifest + checksums
    7. Encrypts with AES-256-GCM
    8. Silent decrypt round-trip verifies the password
    9. Streams the .quodb file as a download
    """
    user = get_current_user(request)
    result = run_export(req.password, user)
    package_path = Path(result["packagePath"])
    package_size = result["packageSizeBytes"]

    filename = f"quodb_export_{datetime.now().strftime('%Y-%m-%d')}.quodb"

    def stream_package():
        with open(package_path, "rb") as f:
            while chunk := f.read(65_536):
                yield chunk
        os.unlink(package_path)

    return StreamingResponse(
        stream_package(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(package_size),
        },
    )


# ─── Import ───────────────────────────────────────────────


@router.post("/import/run", dependencies=[Depends(require_role("master"))])
async def run_import_endpoint(
    file: UploadFile = File(...),
    password: str = Form(...),
    dry_run: bool = Form(False),
    force_system_id: bool = Form(False),
):
    """Import a .quodb package with optional dry-run.

    The import workflow (4 phases):
    Phase 1 — Verify package: decrypt, extract, check checksums, systemId, sequence
    Phase 2 — Self-check: live DB integrity_check, file refs
    Phase 3 — Compare: record dedup via hash, file conflict detection
    Phase 4 — Apply (unless dry-run): copy files, insert records, transactional

    Use dry_run=true to preview what would be imported without making changes.
    Use force_system_id=true to import from a different systemId.
    """
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".quodb")
        os.close(fd)
        with open(temp_path, "wb") as f:
            shutil.copyfileobj(file.file, f, length=1024 * 1024)

        result = run_import(
            Path(temp_path),
            password,
            dry_run=dry_run,
            force_system_id=force_system_id,
        )
        return result
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
