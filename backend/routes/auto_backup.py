"""
backend/routes/auto_backup.py — Auto-backup status and restore endpoints.

Provides:
    - GET  /auto-backup/status   — last backup, next scheduled time
    - GET  /auto-backup/list     — all auto-backups grouped by category
    - POST /auto-backup/restore  — restore from a selected auto-backup
"""

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import JSONResponse

from ..auth import get_current_user, require_role
from ..auto_backup import (
    DAILY_DIR, WEEKLY_DIR, EVENTS_DIR,
    _get_state,
    _last_daily_backup_date,
    AUTO_BACKUP_ROOT,
    retention_sweep,
)
from ..key_manager import get_internal_key, get_current_key_version
from ..export_import import run_import

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auto-backup", tags=["auto-backup"])


def _read_manifest_metadata(p: Path) -> dict | None:
    """Read the exportType and exportedAtUtc from a decrypted manifest.

    To avoid decrypting every file in the list endpoint, we return basic
    file metadata (name, size, mtime) and let the frontend request the
    manifest only for a selected file.
    """
    # For the list endpoint, just return file-level metadata.
    # Full manifest is computed during the restore preflight.
    return None


def _discover_backups() -> dict:
    """Return all auto-backup files grouped by category."""
    result = {
        "daily": [],
        "weekly": [],
        "events": [],
    }

    def _file_info(p: Path) -> dict:
        st = p.stat()
        return {
            "name": p.name,
            "path": str(p.relative_to(AUTO_BACKUP_ROOT)),
            "sizeBytes": st.st_size,
            "modifiedUtc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        }

    for p in sorted(DAILY_DIR.glob("*.quodb"), key=lambda f: f.stat().st_mtime, reverse=True):
        result["daily"].append(_file_info(p))

    for p in sorted(WEEKLY_DIR.glob("*.quodb"), key=lambda f: f.stat().st_mtime, reverse=True):
        result["weekly"].append(_file_info(p))

    for p in sorted(EVENTS_DIR.glob("*.quodb"), key=lambda f: f.stat().st_mtime, reverse=True):
        result["events"].append(_file_info(p))

    return result


@router.get("/status", dependencies=[Depends(require_role("admin", "master"))])
async def auto_backup_status():
    """Return auto-backup subsystem status."""
    last_date = _last_daily_backup_date()
    last_status = _get_state("last_daily_status", "NONE")

    # Next scheduled: tomorrow at 03:00 local time, or today if window hasn't passed
    from ..auto_backup import DEFAULT_BACKUP_HOUR, DEFAULT_BACKUP_MINUTE
    from datetime import timedelta

    now = datetime.now()
    today_scheduled = now.replace(hour=DEFAULT_BACKUP_HOUR, minute=DEFAULT_BACKUP_MINUTE, second=0, microsecond=0)
    if now < today_scheduled:
        next_scheduled = today_scheduled
    else:
        next_scheduled = today_scheduled + timedelta(days=1)

    return {
        "active": True,
        "lastBackup": {
            "date": str(last_date) if last_date else None,
            "status": last_status,
        },
        "nextScheduled": next_scheduled.isoformat(),
        "dailyCount": len(list(DAILY_DIR.glob("*.quodb"))),
        "weeklyCount": len(list(WEEKLY_DIR.glob("*.quodb"))),
        "eventCount": len(list(EVENTS_DIR.glob("*.quodb"))),
    }


@router.get("/list", dependencies=[Depends(require_role("admin", "master"))])
async def auto_backup_list():
    """Return all auto-backup files grouped by category."""
    return _discover_backups()


@router.post("/restore", dependencies=[Depends(require_role("master"))])
async def auto_restore_endpoint(
    request: Request,
    filename: str = Form(...),
    dry_run: bool = Form(False),
    force_system_id: bool = Form(False),
):
    """Restore from an auto-backup file.

    Uses the same import pipeline as manual import (:func:`run_import`),
    but decrypts with the Internal Backup Key instead of a user password.
    """
    user = get_current_user(request)

    # Resolve the backup file path
    backup_path = AUTO_BACKUP_ROOT / filename
    # Normalise to prevent path traversal
    backup_path = backup_path.resolve()
    if not str(backup_path).startswith(str(AUTO_BACKUP_ROOT.resolve())):
        return JSONResponse(status_code=403, content={"detail": "Invalid file path"})
    if not backup_path.exists():
        return JSONResponse(status_code=404, content={"detail": "Backup file not found"})

    # Load the internal key that matches the package's header keyVersion
    from ..export_import import HEADER_SIZE, HEADER_FORMAT
    try:
        with open(backup_path, "rb") as fh:
            header = fh.read(HEADER_SIZE)
        _, _, header_kv, _ = HEADER_FORMAT.unpack(header)
    except Exception as e:
        return JSONResponse(status_code=400, content={"detail": f"Cannot read package header: {e}"})

    # header_kv >= 2 for auto-backup.  key manager version = header_kv - 1.
    km_version = header_kv - 1
    try:
        key = get_internal_key(km_version)
    except FileNotFoundError:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Cannot decrypt backup: key v{km_version} not found. "
                     "The backup was created with a different key version that has been purged."},
        )

    try:
        result = run_import(
            backup_path,
            key,
            dry_run=dry_run,
            force_system_id=force_system_id,
        )
    except Exception as e:
        logger.exception("Auto-backup restore FAILED (file=%s)", filename)
        return JSONResponse(status_code=500, content={"detail": f"Restore failed: {e}"})

    # Tag the result so the frontend knows it came from auto-backup
    result["autoBackupSource"] = filename
    return result
