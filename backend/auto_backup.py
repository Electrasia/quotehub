"""
backend/auto_backup.py — Automatic backup subsystem for QuoteHub.

Provides:
    - Scheduled daily backups
    - Startup catch-up for missed backup windows
    - Retention sweep (daily/weekly/event tiers)
    - Event-based backup trigger (pre-update, pre-import, etc.)
    - Post-update version-transition warning
    - Background scheduler loop

All auto-backups are .quodb-format files (reusing manual export pipeline)
encrypted with the Internal Backup Key via the key manager.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .db import DATA_DIR
from .key_manager import (
    _get_state,
    _set_state,
    _ensure_state_table,
    ensure_internal_key,
    get_internal_key,
    get_current_key_version,
    purge_unused_key_versions,
)

logger = logging.getLogger(__name__)

# ─── Directories ─────────────────────────────────────────────────────────────

AUTO_BACKUP_ROOT = DATA_DIR / "auto-backups"
DAILY_DIR = AUTO_BACKUP_ROOT / "daily"
WEEKLY_DIR = AUTO_BACKUP_ROOT / "weekly"
EVENTS_DIR = AUTO_BACKUP_ROOT / "events"

# Rolling retention limits
MAX_DAILY_BACKUPS = 7
MAX_WEEKLY_BACKUPS = 4
EVENT_RETENTION_DAYS = 45

# Default backup window: 03:00 local time
DEFAULT_BACKUP_HOUR = 3
DEFAULT_BACKUP_MINUTE = 0

# Idempotency window (seconds) for pre-update CLI
IDEMPOTENCY_WINDOW_SEC = 300

# ─── Directory setup ─────────────────────────────────────────────────────────


def _ensure_dirs():
    """Create all auto-backup directories if they do not exist."""
    for d in (DAILY_DIR, WEEKLY_DIR, EVENTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ─── Filename helpers ────────────────────────────────────────────────────────


def _daily_filename() -> str:
    """Generate a unique daily backup filename like ``auto_2026-06-19_03-00-42.quodb``."""
    import secrets
    now = datetime.now()
    return f"auto_{now.strftime('%Y-%m-%d_%H-%M-%S')}_{secrets.token_hex(4)}.quodb"


def _event_filename(tag: str) -> str:
    """Generate a unique event backup filename.

    *tag* is sanitised to remove characters unsafe for filenames.
    """
    import secrets
    safe_tag = re.sub(r'[^\w.-]', '_', tag)
    now = datetime.now()
    return f"{safe_tag}_{now.strftime('%Y-%m-%d_%H-%M-%S')}_{secrets.token_hex(4)}.quodb"


# ─── Core ────────────────────────────────────────────────────────────────────


def run_daily_backup() -> dict | None:
    """Run the scheduled daily backup.

    Steps:
        1. Skip if no data has changed since the last backup.
        2. Encrypt with the current Internal Backup Key (key_version >= 2).
        3. Save to ``DAILY_DIR``.
        4. Run retention sweep.

    Returns the ``run_export()`` result dict, or None if skipped.
    """
    _ensure_dirs()
    ensure_internal_key()  # no-op if already initialised

    from .export_import import run_export

    key = get_internal_key()
    kv = get_current_key_version() + 1  # header keyVersion >= 2 (auto-backup)
    output_path = DAILY_DIR / _daily_filename()

    # User stub — auto-backups are system-triggered
    system_user = {"id": 0, "username": "auto-backup", "role": "system"}

    try:
        result = run_export(
            key,
            system_user,
            output_path=output_path,
            event_tag="daily",
            key_version=kv,
        )
    except Exception:
        logger.exception("Daily backup FAILED")
        _set_state("last_daily_status", "FAILED")
        raise

    _set_state("last_daily_status", "SUCCESS")
    logger.info(
        "Daily backup completed: %s (%.1f MB, %d records)",
        output_path.name,
        result["packageSizeBytes"] / 1_048_576,
        result["recordCount"],
    )

    # Retention sweep
    retention_sweep()
    _promote_weekly_if_sunday()

    return result


def run_event_backup(tag: str) -> dict:
    """Run an event-triggered auto-backup (pre-update, pre-import, etc.).

    The backup is saved to ``EVENTS_DIR`` with a filename derived from *tag*.

    Returns the ``run_export()`` result dict.
    """
    from .export_import import run_export

    _ensure_dirs()
    ensure_internal_key()
    output_path = EVENTS_DIR / _event_filename(tag)

    key = get_internal_key()
    kv = get_current_key_version() + 1  # header keyVersion >= 2
    system_user = {"id": 0, "username": "auto-backup", "role": "system"}

    try:
        result = run_export(
            key,
            system_user,
            output_path=output_path,
            event_tag=tag,
            key_version=kv,
        )
    except Exception:
        logger.exception("Event backup FAILED (tag=%s)", tag)
        raise

    logger.info(
        "Event backup completed: %s (%.1f MB, tag=%s)",
        output_path.name,
        result["packageSizeBytes"] / 1_048_576,
        tag,
    )
    return result


# ─── Weekly promotion ────────────────────────────────────────────────────────


def _promote_weekly_if_sunday():
    """Promote the latest daily backup to weekly/ on Sundays.

    Idempotent — if a weekly backup already exists for today, does nothing.
    """
    today = date.today()
    if today.weekday() != 6:  # 6 = Sunday
        return

    # Check if we already promoted a backup today
    for existing in WEEKLY_DIR.glob("*.quodb"):
        if today.strftime("%Y-%m-%d") in existing.name:
            return  # already promoted today

    dailies = sorted(DAILY_DIR.glob("auto_*.quodb"), key=lambda p: p.stat().st_mtime)
    if not dailies:
        return

    latest = dailies[-1]
    weekly_path = WEEKLY_DIR / latest.name
    shutil.copy2(latest, weekly_path)
    logger.info("Promoted daily backup to weekly: %s", weekly_path.name)


# ─── Retention sweep ─────────────────────────────────────────────────────────


def _find_in_use_key_versions() -> set[int]:
    """Scan all retained auto-backup .quodb headers for their keyVersion values."""
    from .export_import import HEADER_SIZE, HEADER_FORMAT

    in_use: set[int] = set()
    for d in (DAILY_DIR, WEEKLY_DIR, EVENTS_DIR):
        for p in d.glob("*.quodb"):
            try:
                with open(p, "rb") as f:
                    header = f.read(HEADER_SIZE)
                    _, _, kv, _ = HEADER_FORMAT.unpack(header)
                    in_use.add(kv)
            except Exception:
                logger.warning("Could not read keyVersion from %s — skipping", p)
    return in_use


def retention_sweep():
    """Apply the rolling retention policy.

    Daily tier: keep the last 7, delete the rest.
    Weekly tier: keep the last 4, delete the rest.
    Event tier: delete files older than 45 days, EXCEPT keep the last 3
        pre-update backups (they are preserved regardless of age).
    After cleanup, purge key versions that are no longer in use.

    Logs every deletion with filename, age, and reason.
    """
    _ensure_dirs()
    now = time.time()
    deletions: list[dict] = []

    # ── Daily tier ──
    dailies = sorted(DAILY_DIR.glob("auto_*.quodb"), key=lambda p: p.stat().st_mtime)
    if len(dailies) > MAX_DAILY_BACKUPS:
        for p in dailies[:-MAX_DAILY_BACKUPS]:
            age_days = (now - p.stat().st_mtime) / 86_400
            p.unlink()
            deletions.append({
                "file": p.name,
                "type": "daily",
                "age_days": round(age_days, 1),
                "reason": f"exceeded daily limit of {MAX_DAILY_BACKUPS}",
            })

    # ── Weekly tier ──
    weeklies = sorted(WEEKLY_DIR.glob("auto_*.quodb"), key=lambda p: p.stat().st_mtime)
    if len(weeklies) > MAX_WEEKLY_BACKUPS:
        for p in weeklies[:-MAX_WEEKLY_BACKUPS]:
            age_days = (now - p.stat().st_mtime) / 86_400
            p.unlink()
            deletions.append({
                "file": p.name,
                "type": "weekly",
                "age_days": round(age_days, 1),
                "reason": f"exceeded weekly limit of {MAX_WEEKLY_BACKUPS}",
            })

    # ── Event tier: 45-day rule with pre-update exception ──
    # Gather all pre-update backups, sorted newest first
    pre_update_files = sorted(
        EVENTS_DIR.glob("pre-update-*.quodb"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    pre_update_keep: set[Path] = set(pre_update_files[:3])  # last 3 are sacred

    for p in EVENTS_DIR.glob("*.quodb"):
        age_seconds = now - p.stat().st_mtime
        age_days = age_seconds / 86_400
        if age_days <= EVENT_RETENTION_DAYS:
            continue  # young enough
        if p in pre_update_keep:
            continue  # exception: preserve last 3 pre-update

        p.unlink()
        deletions.append({
            "file": p.name,
            "type": "event",
            "age_days": round(age_days, 1),
            "reason": f"older than {EVENT_RETENTION_DAYS} days",
        })

    # ── Key cleanup ──
    in_use = _find_in_use_key_versions()
    # Always keep the current key version even if no file references it yet
    in_use.add(get_current_key_version())
    purge_unused_key_versions(in_use)

    # ── Log ──
    if deletions:
        for d in deletions:
            logger.info(
                "Retention sweep deleted %s: type=%s age=%.1fd reason=%s",
                d["file"], d["type"], d["age_days"], d["reason"],
            )
    else:
        logger.info("Retention sweep — nothing to delete")


# ─── Startup catch-up ────────────────────────────────────────────────────────


def _last_daily_backup_date() -> date | None:
    """Return the date of the last successful daily backup, or None."""
    v = _get_state("last_daily_date")
    if v is None:
        return None
    try:
        return date.fromisoformat(v)
    except (ValueError, TypeError):
        return None


def catch_up_missed_auto_backups():
    """Called once on app startup.

    If no daily backup exists for today, runs one immediately.
    Also handles post-update version-transition detection.
    """
    _ensure_state_table()
    _ensure_dirs()

    today = date.today()

    # ── Daily catch-up ──
    last = _last_daily_backup_date()
    if last is None or last < today:
        logger.info("Startup catch-up: no daily backup for today — running now")
        try:
            run_daily_backup()
            _set_state("last_daily_date", today.isoformat())
        except Exception:
            logger.warning("Startup catch-up daily backup failed — will retry next startup")
    else:
        logger.debug("Startup catch-up: daily backup already exists for today")

    # ── Post-update version transition check ──
    _check_post_upgrade()


# ─── Post-upgrade warning ────────────────────────────────────────────────────


def _check_post_upgrade():
    """Check whether a pre-update backup exists for the version transition.

    On the first launch after an app version change, this checks whether
    a pre-update backup exists for the *previous* version (the stored
    ``last_seen_app_version``).  If absent, a WARNING is logged.

    This is a forensic breadcrumb — it does not block the user.
    """
    from .main import APP_VERSION

    stored = _get_state("last_seen_app_version")
    if stored is None:
        # First ever launch — set baseline, no check needed
        _set_state("last_seen_app_version", APP_VERSION)
        return

    if stored == APP_VERSION:
        return  # no version change

    # Version changed: look for a pre-update backup tagged with the old version
    pattern = f"pre-update-v{stored}_*.quodb"
    found = list(EVENTS_DIR.glob(pattern))
    if not found:
        logger.warning(
            "App version changed %s → %s, but NO pre-update backup found "
            "(pattern=%s). Update may have been performed without the sanctioned script.",
            stored, APP_VERSION, pattern,
        )
    else:
        logger.info(
            "App version changed %s → %s, pre-update backup exists: %s",
            stored, APP_VERSION, found[-1].name,
        )

    _set_state("last_seen_app_version", APP_VERSION)


# ─── Background scheduler ────────────────────────────────────────────────────


async def _scheduler_loop():
    """Background asyncio loop that runs the daily backup at the configured time.

    Checks the time every 60 seconds.  When the target hour+minute is reached,
    runs the daily backup if it hasn't already run today.
    """
    while True:
        now = datetime.now()
        if now.hour == DEFAULT_BACKUP_HOUR and now.minute == DEFAULT_BACKUP_MINUTE:
            last = _last_daily_backup_date()
            if last is None or last < date.today():
                logger.info("Scheduler triggered: running daily backup")
                try:
                    run_daily_backup()
                    _set_state("last_daily_date", date.today().isoformat())
                except Exception:
                    logger.exception("Scheduled daily backup failed")
            await asyncio.sleep(61)  # skip re-check within same minute
        else:
            await asyncio.sleep(60)


# ─── Public startup hook ─────────────────────────────────────────────────────


def start_auto_backup_subsystem():
    """Initialize and start the auto-backup subsystem.

    Called once at app startup (inside the FastAPI lifespan).
    1. Ensures internal key is initialised.
    2. Catches up missed backups.
    3. Launches the background scheduler.
    """
    ensure_internal_key()
    catch_up_missed_auto_backups()

    # Start the async scheduler loop in the background
    loop = asyncio.get_event_loop()
    loop.create_task(_scheduler_loop())
    logger.info("Auto-backup subsystem started")


# ─── Import- / edit-triggered pre-backup (called from route handlers) ────


def pre_import_backup(source_filename: str) -> dict | None:
    """Run a pre-import auto-backup before a manual import.

    Called from the import route handler before ``run_import()``.
    Tagged as ``pre-import`` with the source filename for traceability.
    """
    tag = f"pre-import-{Path(source_filename).stem}"
    return run_event_backup(tag)


def pre_bulk_backup(operation: str) -> dict | None:
    """Run a pre-bulk-operation auto-backup."""
    tag = f"pre-bulk-{operation}"
    return run_event_backup(tag)
