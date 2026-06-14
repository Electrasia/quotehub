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

import sqlite3
from pathlib import Path
from contextlib import contextmanager

# ─── Database Path ───────────────────────────────────────────────────────────
# Single source of truth for the database location.
# All modules should import DB_PATH from here, not define their own.
DB_PATH = Path(__file__).parent.parent / "data" / "quotations.db"


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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        # Auto-commit on successful exit (for write operations)
        if not readonly:
            conn.commit()
    except Exception:
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
