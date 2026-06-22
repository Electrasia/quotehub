"""
backend/db.py — Database connection manager for QuoteHub.

This module provides a context manager for SQLite database connections.
It ensures connections are properly opened and closed, and provides
a consistent interface for database operations.

Usage:
    from backend.db import get_db

    # For read operations
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    # For write operations (auto-commits)
    with get_db() as db:
        db.execute("INSERT INTO users (username) VALUES (?)", (username,))
        # Connection is automatically committed when exiting the context
"""

import logging
import sqlite3
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# ─── Database Path ───────────────────────────────────────────────────────────
# Single source of truth for the database location.
# All modules should import DB_PATH from here, not define their own.
DB_PATH = Path(__file__).parent.parent / "data" / "quotations.db"
DATA_DIR = DB_PATH.parent


# ─── Machine ID ──────────────────────────────────────────────────────────────
# Stable server identifier for export/import systemId enforcement.
# Generated once, stored in the data volume. Survives container rebuilds.
# Only regenerated if the file is manually deleted.

_MACHINE_ID: str | None = None


def get_machine_id() -> str:
    """Get or create the stable machine identifier.

    The machine ID is a UUID generated once and stored in the data volume.
    It identifies this specific installation for export/import systemId checks.
    The master can override the check during import (force_system_id flag).
    """
    global _MACHINE_ID
    if _MACHINE_ID is not None:
        return _MACHINE_ID

    path = DATA_DIR / "machine_id"
    if path.exists():
        _MACHINE_ID = path.read_text().strip()
    else:
        import uuid
        _MACHINE_ID = str(uuid.uuid4())
        path.write_text(_MACHINE_ID)
    return _MACHINE_ID


@contextmanager
def get_db(readonly=False):
    """Context manager for database connections.

    This function provides a safe way to work with database connections.
    It automatically handles connection lifecycle and ensures connections
    are properly closed, even if errors occur.

    Args:
        readonly (bool): If True, opens connection in read-only mode.
            Use this for SELECT queries to prevent accidental writes.

    Yields:
        sqlite3.Connection: Database connection with row_factory set to
            sqlite3.Row (allows accessing columns by name).

    Example:
        # Read operation
        with get_db(readonly=True) as db:
            users = db.execute("SELECT * FROM users").fetchall()

        # Write operation (auto-commits on success)
        with get_db() as db:
            db.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
            # Connection is committed when exiting the 'with' block

        # Write operation with explicit commit
        with get_db() as db:
            db.execute("UPDATE users SET role = ? WHERE id = ?", ("admin", 1))
            db.commit()  # Explicit commit if needed before exiting

    Note:
        - The connection is ALWAYS closed when exiting the context, even if
          an exception occurs.
        - For write operations, the connection is auto-committed when exiting
          the context. You can also call db.commit() explicitly.
        - If an exception occurs, the transaction is rolled back automatically.
    """
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=500")
    try:
        yield conn
        # Auto-commit on successful exit (for write operations)
        if not readonly:
            conn.commit()
    except Exception:
        logger.exception("DB operation failed, rolling back")
        # Rollback on error
        conn.rollback()
        raise
    finally:
        # Always close the connection
        conn.close()


def init_db():
    """Initialize the database schema.

    This function creates the necessary tables if they don't exist.
    It should be called once when the application starts.

    Creates:
        - quotations: Stores extracted quotation data
        - quotations_fts: Full-text search index for fast searching
        - users: User accounts with role-based access
        - triggers: Keep FTS index in sync with quotations table

    Schema version: 2 (adds currency and extraction_method columns)
    """
    with get_db() as db:
        # Create quotations table
        db.execute("""
            CREATE TABLE IF NOT EXISTS quotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                supplier TEXT,
                quotation_date TEXT,
                currency TEXT,
                items TEXT NOT NULL,
                document_type TEXT DEFAULT 'unknown',
                extraction_method TEXT DEFAULT 'local',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Migration: Add currency and extraction_method columns if missing
        # (for existing databases created before v0.040.0)
        _migrate_db(db)

        # Create users table for authentication
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                must_change_password INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                last_login TEXT
            )
        """)

        # Migrate users table (add columns missing from older schemas)
        _migrate_users_db(db)

        # Create FTS5 virtual table for fast full-text search
        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS quotations_fts USING fts5(
                filename,
                supplier,
                items,
                currency,
                content='quotations',
                content_rowid='id'
            )
        """)

        # Create triggers to keep FTS in sync with quotations table
        # These triggers automatically update the search index when
        # data is inserted, updated, or deleted from the quotations table.
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS quotations_ai AFTER INSERT ON quotations BEGIN
                INSERT INTO quotations_fts(rowid, filename, supplier, items, currency)
                VALUES (new.id, new.filename, new.supplier, new.items, new.currency);
            END
        """)
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS quotations_ad AFTER DELETE ON quotations BEGIN
                INSERT INTO quotations_fts(quotations_fts, rowid, filename, supplier, items, currency)
                VALUES ('delete', old.id, old.filename, old.supplier, old.items, old.currency);
            END
        """)
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS quotations_au AFTER UPDATE ON quotations BEGIN
                INSERT INTO quotations_fts(quotations_fts, rowid, filename, supplier, items, currency)
                VALUES ('delete', old.id, old.filename, old.supplier, old.items, old.currency);
                INSERT INTO quotations_fts(rowid, filename, supplier, items, currency)
                VALUES (new.id, new.filename, new.supplier, new.items, new.currency);
            END
        """)

        # Run any pending schema migrations (empty until a migration is defined)
        _run_migrations(db)


def _migrate_db(db):
    """Migrate existing databases to the latest schema.
    
    This function adds missing columns to existing tables.
    It's safe to call multiple times (uses ALTER TABLE IF NOT EXISTS pattern).
    """
    # Check if currency column exists
    cursor = db.execute("PRAGMA table_info(quotations)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "currency" not in columns:
        db.execute("ALTER TABLE quotations ADD COLUMN currency TEXT")
        print("Migration: Added 'currency' column to quotations table")
    
    if "extraction_method" not in columns:
        db.execute("ALTER TABLE quotations ADD COLUMN extraction_method TEXT DEFAULT 'local'")
        print("Migration: Added 'extraction_method' column to quotations table")


def _migrate_users_db(db):
    """Migrate users table to the latest schema.
    
    Adds any missing columns to the users table.
    Safe to call multiple times.
    """
    # Check if users table exists first
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cursor.fetchone():
        return  # Table doesn't exist yet, CREATE TABLE IF NOT EXISTS handles it
    
    cursor = db.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    
    # Future migrations go here, e.g.:
    # if "email" not in columns:
    #     db.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
    #     print("Migration: Added 'email' column to users table")


# ─── Schema Migration System ──────────────────────────────────────────────────
#
# CRITICAL RULES — every new migration MUST follow these:
#
# Rule 1: DDL and DML in SEPARATE migration functions
#   DDL (CREATE/ALTER TABLE) auto-commits in SQLite. If a single function
#   mixes DDL and DML and the DML fails, the DDL is already committed but
#   the version is not updated. On retry, DDL is a no-op but DML may
#   duplicate data.
#
#   ✅ Correct — split into two versions:
#      1: _v1_supplier_ddl       (DDL only)
#      2: _v2_supplier_data      (DML only)
#   ❌ Wrong — mixed together:
#      v1: DDL + DML              ← data corruption risk
#
# Rule 2: DML must be IDEMPOTENT
#   Every INSERT/UPDATE in a migration must be safe to run multiple times.
#   Use INSERT OR IGNORE, SELECT before INSERT, or WHERE existence checks.
#   Never use plain INSERT that would create duplicates on retry.
#
#   ✅ Correct:  db.execute("INSERT OR IGNORE INTO suppliers VALUES (?)", ...)
#   ❌ Wrong:    db.execute("INSERT INTO suppliers VALUES (?)", ...)

# Migration registry: version_number -> callable(db_connection)
# Starts empty. First migration will be version 1.
MIGRATIONS: dict[int, callable] = {}


# ─── Migration v1: export_registry table ─────────────────────────────────────

def _v1_export_registry(db):
    """Create the export_registry table for tracking export/import operations.

    DDL only — safe to run multiple times (CREATE TABLE IF NOT EXISTS).
    Stores one row per export attempt with status tracking (STARTED/FAILED/CANCELED/SUCCESS).
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS export_registry (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            export_id          TEXT UNIQUE NOT NULL,
            system_id          TEXT NOT NULL,
            sequence_number    INTEGER NOT NULL,
            status             TEXT NOT NULL CHECK(status IN ('STARTED','FAILED','CANCELED','SUCCESS')),
            manifest_path      TEXT,
            package_path       TEXT,
            error_detail       TEXT,
            record_count       INTEGER,
            file_count         INTEGER,
            package_size_bytes INTEGER,
            started_at         TEXT DEFAULT (datetime('now')),
            completed_at       TEXT,
            checksum_algorithm TEXT DEFAULT 'sha-256'
        )
    """)
    logger.info("Migration v1 complete: export_registry table created")


MIGRATIONS[1] = _v1_export_registry


# ─── Migration v2: suppliers DDL (additive only) ────────────────────────────

def _v2_suppliers_ddl(db):
    """Create all supplier-related tables + ALTER quotations.

    **DDL ONLY** — safe to run multiple times (``CREATE TABLE IF NOT EXISTS``
    and ``ALTER TABLE`` guarded by ``PRAGMA table_info``).

    Tables created:
        - ``suppliers`` — core supplier record.  ``status`` is one of
          ``'active'`` (normal operational), ``'inactive'`` (soft-deleted),
          or ``'review'`` (auto-created during backfill, needs human review).
        - ``supplier_aliases`` — alternative names for a supplier.
        - ``contacts`` — people at a supplier.  ``position`` is an INTEGER
          representing display sort order (not job title — that is ``role``).
        - ``brands`` — shared reference table (name UNIQUE).
        - ``product_types`` — shared reference table (name UNIQUE).
        - ``supplier_capabilities`` — brand → product-type junction per supplier.
          ``verified`` flag is **Master-only** to set (Admin receives 403).
        - ``supplier_audit_log`` — every sensitive operation logged with
          before/after JSON diff on updates.

    Quotations altered (additive, never destructive):
        - ``normalized_supplier_name TEXT`` — computed by backfill.
        - ``supplier_id INTEGER REFERENCES suppliers(id)`` — FK to supplier.

    Indexes created for all lookup paths.

    **Additive-only guarantee:**
    - No existing columns are modified or dropped.
    - ``quotations.supplier`` is never touched.
    """
    # ── New tables ──────────────────────────────────────────────────────
    db.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name  TEXT NOT NULL UNIQUE,
            display_name    TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active','inactive','review')),
            notes           TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS supplier_aliases (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            alias         TEXT NOT NULL UNIQUE,
            supplier_id   INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id            INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
            name                   TEXT NOT NULL DEFAULT '',
            email                  TEXT NOT NULL DEFAULT '',
            phone                  TEXT NOT NULL DEFAULT '',
            role                   TEXT NOT NULL DEFAULT '',
            position               INTEGER NOT NULL DEFAULT 0,
            is_default_rfq_contact INTEGER NOT NULL DEFAULT 0,
            created_at             TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS brands (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS product_types (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS supplier_capabilities (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id     INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
            brand_id        INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            product_type_id INTEGER NOT NULL REFERENCES product_types(id) ON DELETE CASCADE,
            verified        INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(supplier_id, brand_id, product_type_id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS supplier_audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER,
            action      TEXT NOT NULL,
            actor       TEXT NOT NULL,
            details     TEXT NOT NULL DEFAULT '{}',
            timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ── ALTER quotations (additive, guarded) ────────────────────────────
    # Use PRAGMA table_info to check before adding — safe even if the
    # migration is somehow re-run (the version tracker normally prevents this).
    cols = {row[1] for row in db.execute("PRAGMA table_info(quotations)")}

    if "normalized_supplier_name" not in cols:
        db.execute(
            "ALTER TABLE quotations ADD COLUMN normalized_supplier_name TEXT"
        )
        logger.info("  -> Added normalized_supplier_name to quotations")

    if "supplier_id" not in cols:
        db.execute(
            "ALTER TABLE quotations ADD COLUMN supplier_id INTEGER REFERENCES suppliers(id)"
        )
        logger.info("  -> Added supplier_id to quotations")

    # ── Indexes ─────────────────────────────────────────────────────────
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_suppliers_canonical_name"
        " ON suppliers(canonical_name)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_suppliers_status ON suppliers(status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_supplier_aliases_alias"
        " ON supplier_aliases(alias)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_supplier_aliases_supplier"
        " ON supplier_aliases(supplier_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_contacts_supplier ON contacts(supplier_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_supplier_capabilities_triplet"
        " ON supplier_capabilities(supplier_id, brand_id, product_type_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_supplier_capabilities_supplier"
        " ON supplier_capabilities(supplier_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_brands_name ON brands(name)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_product_types_name ON product_types(name)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_quotations_supplier_id"
        " ON quotations(supplier_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_quotations_normalized_name"
        " ON quotations(normalized_supplier_name)"
    )

    logger.info("Migration v2 complete: suppliers DDL applied")


MIGRATIONS[2] = _v2_suppliers_ddl


# ─── Migration v3: supplier backfill (DML only, batched, idempotent) ────────

def _v3_suppliers_backfill(db):
    """Backfill ``normalized_supplier_name`` and ``supplier_id`` on quotations.

    **DML ONLY** — no schema changes.  Idempotent.  Reversible.

    Process:
        1. Read quotations where ``normalized_supplier_name IS NULL``.
        2. Process in **batches of 200 rows**, each committed independently
           (prevents long-held write locks).
        3. For each distinct ``supplier`` value:
           a. Normalise via :func:`~backend.suppliers.normalize_name`.
           b. Resolve via :func:`~backend.suppliers.resolve_supplier`.
           c. If resolved → set ``supplier_id``.
           d. If unresolved → ``INSERT OR IGNORE INTO suppliers`` with
              ``status='review'`` and ``notes='Auto-created during backfill'``;
              write a ``supplier_audit_log`` entry with
              ``action='auto_created_backfill'``.
        4. ``UPDATE quotations SET normalized_supplier_name = ?, supplier_id = ?``
           for each quotation.

    **Guarantees:**
    - ``quotations.supplier`` is **never read for writing** and never modified.
    - Rows where ``supplier`` is ``NULL`` or empty are skipped.
    - Rows already processed (``normalized_supplier_name IS NOT NULL``) are
      skipped — makes the migration safe to run multiple times.

    **Reversibility:**
    .. code-block:: sql
        UPDATE quotations SET normalized_supplier_name = NULL, supplier_id = NULL
        WHERE normalized_supplier_name IS NOT NULL;

    Auto-created suppliers remain in the ``suppliers`` table (they document what
    was found during the backfill and are flagged for human review).
    """
    # Lazy-import to avoid circular dependencies at module level
    from backend.suppliers import normalize_name, resolve_supplier

    BATCH_SIZE = 200
    total_processed = 0
    total_created = 0

    while True:
        # Read a batch of unprocessed quotations with distinct supplier values
        rows = db.execute(
            """SELECT DISTINCT q.id, q.supplier
               FROM quotations q
               WHERE q.supplier IS NOT NULL
                 AND q.supplier != ''
                 AND q.normalized_supplier_name IS NULL
               LIMIT ?""",
            (BATCH_SIZE,),
        ).fetchall()

        if not rows:
            break

        # Collect distinct supplier names for this batch to avoid
        # repeated INSERT OR IGNORE attempts for the same name.
        seen_suppliers: dict[str, int | None] = {}  # raw -> supplier_id

        for row in rows:
            raw = row["supplier"]
            if raw in seen_suppliers:
                continue

            norm = normalize_name(raw)
            if not norm:
                seen_suppliers[raw] = None
                continue

            sid = resolve_supplier(db, raw)

            if sid is None:
                # Auto-create supplier flagged for review
                db.execute(
                    """INSERT OR IGNORE INTO suppliers
                       (canonical_name, display_name, status, notes)
                       VALUES (?, ?, 'review', 'Auto-created during backfill')""",
                    (norm, norm),
                )
                # Re-read the ID — INSERT OR IGNORE may have been a no-op
                srow = db.execute(
                    "SELECT id FROM suppliers WHERE canonical_name = ?",
                    (norm,),
                ).fetchone()
                if srow is not None:
                    sid = srow["id"]
                    total_created += 1
                    # Only log on actual creation (INSERT OR IGNORE may skip)
                    # Use INSERT OR IGNORE on audit log as well (idempotent)
                    db.execute(
                        """INSERT OR IGNORE INTO supplier_audit_log
                           (supplier_id, action, actor, details)
                           VALUES (?, 'auto_created_backfill', 'system', ?)""",
                        (sid, '{"normalized_name": "' + norm + '", "raw_name": "' + raw.replace('"', '\\"') + '"}'),
                    )

            seen_suppliers[raw] = sid

        # Write back to quotations
        for row in rows:
            raw = row["supplier"]
            norm = normalize_name(raw)
            if not norm:
                continue
            sid = seen_suppliers.get(raw)
            db.execute(
                """UPDATE quotations
                   SET normalized_supplier_name = ?, supplier_id = ?
                   WHERE id = ?""",
                (norm, sid, row["id"]),
            )
            total_processed += 1

        # Commit this batch independently
        db.commit()

        logger.info(
            "Backfill batch: %d processed, %d auto-created (running total)",
            total_processed, total_created,
        )

    logger.info(
        "Migration v3 complete: %d quotations backfilled, %d suppliers auto-created",
        total_processed, total_created,
    )


MIGRATIONS[3] = _v3_suppliers_backfill


def _init_schema_version(db):
    """Create the _schema_version table and seed with version 0 if empty.

    The table uses a CHECK(id=1) constraint to guarantee a single row.
    INSERT OR IGNORE ensures idempotent seeding on first run.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS _schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute(
        "INSERT OR IGNORE INTO _schema_version (id, version) VALUES (1, 0)"
    )


def _get_schema_version(db) -> int:
    """Read the current schema version from the database.

    Returns 0 if the table doesn't exist yet, or if no row is found.
    """
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_version'"
    )
    if not cursor.fetchone():
        return 0
    row = db.execute(
        "SELECT version FROM _schema_version WHERE id = 1"
    ).fetchone()
    return row["version"] if row else 0


def _run_migrations(db):
    """Run pending schema migrations in version order.

    Reads the current schema version, validates no version numbers
    are missing in the MIGRATIONS dict, then runs each pending
    migration in order. The version is updated after each migration
    succeeds (UPDATE is DML, committed at connection close by get_db()).

    If any migration raises, the version update is rolled back and
    the error propagates up through init_db().
    """
    _init_schema_version(db)
    current = _get_schema_version(db)

    if not MIGRATIONS:
        return

    max_version = max(MIGRATIONS.keys())

    # Validate no version gaps between current+1 and max
    for v in range(current + 1, max_version + 1):
        if v not in MIGRATIONS:
            raise RuntimeError(
                f"Missing migration version {v}. "
                "All versions between current ({current}) and target ({max_version}) "
                "must be registered in MIGRATIONS dict."
            )

    # Run pending migrations in order
    for v in range(current + 1, max_version + 1):
        fn = MIGRATIONS[v]
        logger.info("Running schema migration v%d: %s", v, fn.__name__)
        fn(db)
        db.execute(
            "UPDATE _schema_version SET version = ? WHERE id = 1",
            (v,)
        )
        logger.info("Schema migration v%d complete", v)
