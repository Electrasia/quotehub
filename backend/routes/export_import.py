"""
backend/routes/export_import.py — Encrypted export/import endpoints.

Provides:
    - Export password management (set, check, forgot-recovery)
    - Encrypted .quodb export download
    - Encrypted .quodb import with dry-run support

All exports and imports are encrypted with AES-256-GCM and require a
master-set export password. The password uses bcrypt and PBKDF2 for
key derivation (600 000 iterations, OWASP 2023 recommendation).

Import supports:
    - Dry-run mode (analyze without applying)
    - systemId mismatch override (force_system_id flag)
    - Duplicate detection via deterministic record hash
    - File conflict detection (existing file with different SHA-256)
    - Transactional apply with automatic rollback on error
"""

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ..auth import require_role
from ..export_import import (
    export_password_exists,
    set_export_password,
    run_export,
    run_import,
)

router = APIRouter(tags=["export-import"])


# ─── Models ────────────────────────────────────────────────


class ExportPasswordRequest(BaseModel):
    """Set, change, or reset the export password."""
    new_password: str
    current_password: str | None = None
    login_password: str | None = None


class ExportRunRequest(BaseModel):
    """Trigger an encrypted export."""
    password: str


# ─── Export Password Management ───────────────────────────


@router.post("/export-password", dependencies=[Depends(require_role("master"))])
async def set_export_password_endpoint(req: ExportPasswordRequest):
    """Set, change, or reset the export password.

    Three modes:
    - No current_password or login_password → first-time setup
    - current_password provided → normal change (verifies old password)
    - login_password provided → forgot recovery (verifies master login)
    """
    return set_export_password(
        new_password=req.new_password,
        current_password=req.current_password,
        login_password=req.login_password,
    )


@router.get("/export-password/status", dependencies=[Depends(require_role("admin", "master"))])
async def export_password_status():
    """Check whether an export password has been set."""
    return {"password_set": export_password_exists()}


# ─── Export ───────────────────────────────────────────────


@router.post("/export/run", dependencies=[Depends(require_role("admin", "master"))])
async def run_export_endpoint(req: ExportRunRequest):
    """Run encrypted export and download the .quodb package.

    The export workflow:
    1. Verifies the export password
    2. Runs PRAGMA integrity_check on the database
    3. Reads all records and verifies referenced files exist in archive
    4. Reports orphan files on disk (warnings, not blocking)
    5. Snapshots the DB via sqlite3.backup()
    6. Copies files with streaming SHA-256
    7. Builds a ZIP64 archive with manifest + checksums
    8. Encrypts with AES-256-GCM
    9. Streams the .quodb file as a download
    """
    result = run_export(req.password)
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


@router.post("/import/run", dependencies=[Depends(require_role("admin", "master"))])
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
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

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
