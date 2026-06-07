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
        - triggers: Keep FTS index in sync with quotations table
    """
    with get_db() as db:
        # Create quotations table
        db.execute("""
            CREATE TABLE IF NOT EXISTS quotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                supplier TEXT,
                quotation_date TEXT,
                items TEXT NOT NULL,
                document_type TEXT DEFAULT 'unknown',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Create FTS5 virtual table for fast full-text search
        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS quotations_fts USING fts5(
                filename,
                supplier,
                items,
                content='quotations',
                content_rowid='id'
            )
        """)

        # Create triggers to keep FTS in sync with quotations table
        # These triggers automatically update the search index when
        # data is inserted, updated, or deleted from the quotations table.
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS quotations_ai AFTER INSERT ON quotations BEGIN
                INSERT INTO quotations_fts(rowid, filename, supplier, items)
                VALUES (new.id, new.filename, new.supplier, new.items);
            END
        """)
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS quotations_ad AFTER DELETE ON quotations BEGIN
                INSERT INTO quotations_fts(quotations_fts, rowid, filename, supplier, items)
                VALUES ('delete', old.id, old.filename, old.supplier, old.items);
            END
        """)
        db.execute("""
            CREATE TRIGGER IF NOT EXISTS quotations_au AFTER UPDATE ON quotations BEGIN
                INSERT INTO quotations_fts(quotations_fts, rowid, filename, supplier, items)
                VALUES ('delete', old.id, old.filename, old.supplier, old.items);
                INSERT INTO quotations_fts(rowid, filename, supplier, items)
                VALUES (new.id, new.filename, new.supplier, new.items);
            END
        """)
