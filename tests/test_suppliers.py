"""tests/test_suppliers.py — Phase 1: Normalization, resolution, backfill.

Tests implemented in this phase:
    - TestNormalizeName …… unit tests for ``normalize_name()``
    - TestResolveSupplier … read-only resolution (canonical + alias match)
    - TestBackfill ………… backfill populates ``normalized_supplier_name``/``supplier_id``
    - TestBackfillIdempotency … run backfill twice, confirm no changes
    - TestQuotationRegression … confirm ``quotations.supplier`` is never modified
"""

import json
import sqlite3

import pytest

from backend.suppliers import normalize_name, resolve_supplier


# =============================================================================
# TestNormalizeName — pure unit tests (no DB)
# =============================================================================

class TestNormalizeName:
    """Unit tests for :func:`normalize_name`."""

    # --- Edge cases ----------------------------------------------------------

    def test_none_input(self):
        assert normalize_name(None) == ""

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_whitespace_only(self):
        assert normalize_name("   ") == ""

    # --- Whitespace ----------------------------------------------------------

    def test_trims_leading_trailing_whitespace(self):
        assert normalize_name("  Acme Corp  ") == "acme corp"

    def test_collapses_internal_spaces(self):
        assert normalize_name("Acme   Corp") == "acme corp"

    def test_tabs_and_newlines(self):
        assert normalize_name("Acme\tCorp\nLtd") == "acme corp ltd"

    # --- Case -----------------------------------------------------------------

    def test_lowercase(self):
        assert normalize_name("ACME CORPORATION") == "acme corporation"

    def test_mixed_case(self):
        assert normalize_name("AcMe CoRp") == "acme corp"

    # --- Punctuation ----------------------------------------------------------

    def test_strips_periods(self):
        assert normalize_name("Acme Corp.") == "acme corp"

    def test_strips_commas(self):
        assert normalize_name("Acme, Corp") == "acme corp"

    def test_strips_quotes(self):
        assert normalize_name('"Acme Corp"') == "acme corp"

    def test_strips_parentheses(self):
        assert normalize_name("Acme Corp (HK)") == "acme corp hk"

    def test_strips_multiple_punctuation(self):
        assert normalize_name("A.C.M.E. Corp., Ltd.") == "acme corp ltd"

    # --- Characters that should be preserved ----------------------------------

    def test_preserves_hyphens(self):
        assert normalize_name("Acme-Corp") == "acme-corp"

    def test_preserves_apostrophes(self):
        assert normalize_name("O'Brien Supply") == "o'brien supply"

    def test_preserves_hyphenated_compound(self):
        assert normalize_name("State-of-the-Art Electronics") == "state-of-the-art electronics"

    def test_preserves_apostrophe_possessive(self):
        assert normalize_name("McDonald's Supply") == "mcdonald's supply"

    # --- Unicode ---------------------------------------------------------------

    def test_unicode(self):
        assert normalize_name("Müller & Söhne") == "müller  söhne"

    def test_unicode_with_accents(self):
        assert normalize_name("Électricité de France") == "électricité de france"

    # --- Already clean --------------------------------------------------------

    def test_already_clean(self):
        assert normalize_name("acme corp") == "acme corp"

    def test_single_word(self):
        assert normalize_name("Acme") == "acme"

    def test_numbers(self):
        assert normalize_name("Supplier 2024") == "supplier 2024"


# =============================================================================
# Common fixture helpers
# =============================================================================

def _create_suppliers_schema(conn):
    """Create the suppliers-related schema (same DDL as MIGRATIONS[2])."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name  TEXT NOT NULL UNIQUE,
            display_name    TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active','inactive','review')),
            notes           TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS supplier_aliases (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            alias         TEXT NOT NULL UNIQUE,
            supplier_id   INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS supplier_audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER,
            action      TEXT NOT NULL,
            actor       TEXT NOT NULL,
            details     TEXT NOT NULL DEFAULT '{}',
            timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_suppliers_canonical_name
            ON suppliers(canonical_name);
        CREATE INDEX IF NOT EXISTS idx_supplier_aliases_alias
            ON supplier_aliases(alias);
        CREATE INDEX IF NOT EXISTS idx_supplier_aliases_supplier
            ON supplier_aliases(supplier_id);
    """)


# =============================================================================
# TestResolveSupplier — strictly read-only resolution
# =============================================================================

@pytest.fixture
def resolve_db(tmp_path):
    """Temp SQLite DB seeded with suppliers and aliases for resolve tests."""
    db_path = tmp_path / "resolve_test.db"
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    _create_suppliers_schema(conn)

    conn.execute(
        "INSERT INTO suppliers (canonical_name, display_name, status) VALUES (?, ?, ?)",
        ("acme corp", "Acme Corporation", "active"),
    )
    conn.execute(
        "INSERT INTO suppliers (canonical_name, display_name, status) VALUES (?, ?, ?)",
        ("beta inc", "Beta Incorporated", "active"),
    )
    # "beta" is an alias for Beta Inc
    conn.execute(
        "INSERT INTO supplier_aliases (alias, supplier_id) VALUES (?, ?)",
        ("beta", 2),
    )
    # "beta incorporated" also resolves to Beta Inc
    conn.execute(
        "INSERT INTO supplier_aliases (alias, supplier_id) VALUES (?, ?)",
        ("beta incorporated", 2),
    )
    conn.commit()
    yield conn
    conn.close()


class TestResolveSupplier:
    """Tests for :func:`resolve_supplier` — strictly read-only."""

    # --- Canonical name match -------------------------------------------------

    def test_match_canonical_name(self, resolve_db):
        """Exact canonical_name match returns the supplier ID."""
        sid = resolve_supplier(resolve_db, "Acme Corp")
        assert sid == 1

    def test_match_canonical_name_normalized(self, resolve_db):
        """Input needs normalization before matching canonical_name."""
        sid = resolve_supplier(resolve_db, "  ACME CORP.  ")
        assert sid == 1

    def test_match_canonical_name_case_insensitive(self, resolve_db):
        """Case differences are resolved by normalization."""
        sid = resolve_supplier(resolve_db, "ACME CORPORATION")
        # "acme corporation" does not match "acme corp" — they're different
        # after normalization. This is correct — no fuzzy matching.
        assert sid is None

    # --- Alias match ----------------------------------------------------------

    def test_match_alias(self, resolve_db):
        """An alias that matches exactly returns the supplier ID."""
        sid = resolve_supplier(resolve_db, "beta")
        assert sid == 2

    def test_match_alias_full_name(self, resolve_db):
        """Alias 'beta incorporated' resolves to Beta Inc."""
        sid = resolve_supplier(resolve_db, "beta incorporated")
        assert sid == 2

    def test_match_alias_normalized(self, resolve_db):
        """Alias matching also normalizes the input."""
        sid = resolve_supplier(resolve_db, "  BETA  ")
        assert sid == 2

    # --- No match -------------------------------------------------------------

    def test_no_match_returns_none(self, resolve_db):
        """Unmatched name returns None."""
        sid = resolve_supplier(resolve_db, "Gamma Ltd")
        assert sid is None

    def test_empty_input_returns_none(self, resolve_db):
        """Empty or whitespace-only input returns None."""
        assert resolve_supplier(resolve_db, "") is None
        assert resolve_supplier(resolve_db, "   ") is None

    def test_none_input_returns_none(self, resolve_db):
        """None input returns None."""
        assert resolve_supplier(resolve_db, None) is None  # type: ignore[arg-type]

    # --- Strictly read-only guarantee -----------------------------------------

    def test_read_only_no_write(self, resolve_db):
        """Confirm that resolve_supplier never writes to the database.

        The suppliers table should have exactly the same rows before and
        after calling resolve_supplier with an unmatched name.
        """
        before = resolve_db.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"]

        resolve_supplier(resolve_db, "NonExistent Supplier XYZ-999")

        after = resolve_db.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"]
        assert before == after, "resolve_supplier created a supplier row (violation of read-only)"

    def test_read_only_unrelated_name(self, resolve_db):
        """Resolving various names, matched or not, never writes."""
        before = resolve_db.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"]

        for name in ["Acme Corp", "beta", "no-such-supplier-42", "", "  XYZ  "]:
            resolve_supplier(resolve_db, name)

        after = resolve_db.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"]
        assert before == after


# =============================================================================
# Backfill test fixtures
# =============================================================================

@pytest.fixture
def backfill_db(tmp_path):
    """Temp SQLite database ready for backfill testing.

    Schema:
        - ``quotations`` (base table WITHOUT supplier columns added yet)
        - Suppliers schema (same DDL as MIGRATIONS[2])

    Seeded with:
        - One pre-existing supplier: ``acme corp`` (canonical) — active
        - 6 quotations with various supplier name patterns:
            q1  "Acme Corp"  → should resolve (matches canonical)
            q2  "Beta Inc"   → should auto-create supplier with status='review'
            q3  "Gamma Ltd"  → should auto-create supplier with status='review'
            q4  "  Acme Corp  " → should resolve (same canonical after norm)
            q5  "" (empty)   → should be skipped
            q6  (NULL)       → should be skipped
    """
    db_path = tmp_path / "backfill_test.db"
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=500")

    # Create base quotations table WITHOUT supplier columns
    conn.execute("""
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
    conn.commit()

    # Apply MIGRATIONS[2] (suppliers DDL — creates tables + ALTER quotations)
    from backend.db import MIGRATIONS
    MIGRATIONS[2](conn)

    # Seed a pre-existing supplier that "Acme Corp" should match
    conn.execute(
        "INSERT INTO suppliers (canonical_name, display_name, status) VALUES (?, ?, ?)",
        ("acme corp", "Acme Corporation", "active"),
    )

    # Seed quotations
    samples = [
        {
            "filename": "q1.pdf",
            "supplier": "Acme Corp",
            "quotation_date": "2025-01-01",
            "currency": "USD",
            "document_type": "QUO",
            "extraction_method": "local",
            "items": [{"model": "A", "brand": "Acme", "description": "Item A", "unit_price": 10.0}],
        },
        {
            "filename": "q2.pdf",
            "supplier": "Beta Inc",
            "quotation_date": "2025-02-01",
            "currency": "USD",
            "document_type": "QUO",
            "extraction_method": "local",
            "items": [{"model": "B", "brand": "Beta", "description": "Item B", "unit_price": 20.0}],
        },
        {
            "filename": "q3.pdf",
            "supplier": "Gamma Ltd",
            "quotation_date": "2025-03-01",
            "currency": "USD",
            "document_type": "QUO",
            "extraction_method": "local",
            "items": [{"model": "C", "brand": "Gamma", "description": "Item C", "unit_price": 30.0}],
        },
        {
            "filename": "q4.pdf",
            "supplier": "  Acme Corp  ",
            "quotation_date": "2025-04-01",
            "currency": "USD",
            "document_type": "QUO",
            "extraction_method": "local",
            "items": [{"model": "D", "brand": "Acme", "description": "Item D", "unit_price": 40.0}],
        },
        {
            "filename": "q5.pdf",
            "supplier": "",
            "quotation_date": "2025-05-01",
            "currency": "USD",
            "document_type": "QUO",
            "extraction_method": "local",
            "items": [{"model": "E", "brand": "X", "description": "Item E", "unit_price": 50.0}],
        },
        {
            "filename": "q6.pdf",
            "supplier": None,
            "quotation_date": "2025-06-01",
            "currency": "USD",
            "document_type": "QUO",
            "extraction_method": "local",
            "items": [{"model": "F", "brand": "Z", "description": "Item F", "unit_price": 60.0}],
        },
    ]
    for q in samples:
        conn.execute(
            """INSERT INTO quotations
               (filename, supplier, quotation_date, currency, items,
                document_type, extraction_method)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (q["filename"], q["supplier"], q["quotation_date"],
             q["currency"], json.dumps(q["items"]),
             q["document_type"], q["extraction_method"]),
        )
    conn.commit()
    yield conn
    conn.close()


# =============================================================================
# TestBackfill
# =============================================================================

class TestBackfill:
    """Tests for the backfill migration (MIGRATIONS[3])."""

    # --- Basic backfill ------------------------------------------------------

    def test_backfill_sets_normalized_name(self, backfill_db):
        """Backfill populates normalized_supplier_name."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)

        # q1: "Acme Corp" → "acme corp"
        row = backfill_db.execute(
            "SELECT normalized_supplier_name FROM quotations WHERE filename = ?",
            ("q1.pdf",),
        ).fetchone()
        assert row is not None
        assert row["normalized_supplier_name"] == "acme corp"

    def test_backfill_resolves_existing_supplier(self, backfill_db):
        """Backfill sets supplier_id for quotations matching existing suppliers."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)

        # q1: "Acme Corp" → supplier_id = 1 (pre-seeded)
        row = backfill_db.execute(
            "SELECT supplier_id FROM quotations WHERE filename = ?",
            ("q1.pdf",),
        ).fetchone()
        assert row is not None
        assert row["supplier_id"] == 1

    def test_backfill_handles_whitespace_variants(self, backfill_db):
        """'  Acme Corp  ' resolves to the same supplier as 'Acme Corp'."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)

        # q4: "  Acme Corp  " → same supplier_id as q1
        row = backfill_db.execute(
            "SELECT normalized_supplier_name, supplier_id FROM quotations WHERE filename = ?",
            ("q4.pdf",),
        ).fetchone()
        assert row is not None
        assert row["normalized_supplier_name"] == "acme corp"
        assert row["supplier_id"] == 1

    def test_backfill_auto_creates_unmatched_supplier(self, backfill_db):
        """Unmatched supplier names create new supplier records with status='review'."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)

        # "Beta Inc" was not pre-seeded → should auto-create
        row = backfill_db.execute(
            "SELECT id, status, notes FROM suppliers WHERE canonical_name = ?",
            ("beta inc",),
        ).fetchone()
        assert row is not None, "Supplier 'beta inc' should have been auto-created"
        assert row["status"] == "review"
        assert "Auto-created during backfill" in row["notes"]

    def test_backfill_skips_empty_supplier(self, backfill_db):
        """Rows with empty or NULL supplier are skipped."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)

        for fn in ("q5.pdf", "q6.pdf"):
            row = backfill_db.execute(
                "SELECT normalized_supplier_name, supplier_id FROM quotations WHERE filename = ?",
                (fn,),
            ).fetchone()
            assert row is not None
            assert row["normalized_supplier_name"] is None
            assert row["supplier_id"] is None

    def test_backfill_creates_audit_log_entries(self, backfill_db):
        """Auto-created suppliers get a supplier_audit_log entry."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)

        rows = backfill_db.execute(
            "SELECT * FROM supplier_audit_log WHERE action = ?",
            ("auto_created_backfill",),
        ).fetchall()
        # "Beta Inc" and "Gamma Ltd" should have been auto-created
        assert len(rows) == 2
        actions = {row["action"] for row in rows}
        assert "auto_created_backfill" in actions
        # Confirm actor is 'system'
        actors = {row["actor"] for row in rows}
        assert "system" in actors

    def test_backfill_links_new_supplier_to_quotation(self, backfill_db):
        """Auto-created supplier IDs are correctly linked to quotations."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)

        # q2: "Beta Inc" should be linked to the auto-created supplier
        q2 = backfill_db.execute(
            "SELECT supplier_id FROM quotations WHERE filename = ?",
            ("q2.pdf",),
        ).fetchone()
        assert q2 is not None
        assert q2["supplier_id"] is not None

        # Verify the linked supplier is the auto-created one
        s = backfill_db.execute(
            "SELECT id, canonical_name FROM suppliers WHERE canonical_name = ?",
            ("beta inc",),
        ).fetchone()
        assert s is not None
        assert q2["supplier_id"] == s["id"]

    def test_backfill_normalized_name_is_correct(self, backfill_db):
        """Verify the computed normalized_supplier_name is correct."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)

        row = backfill_db.execute(
            "SELECT normalized_supplier_name FROM quotations WHERE filename = ?",
            ("q3.pdf",),
        ).fetchone()
        assert row is not None
        assert row["normalized_supplier_name"] == "gamma ltd"

    def test_backfill_does_not_touch_supplier_column(self, backfill_db):
        """The original supplier column is never modified."""
        from backend.db import MIGRATIONS

        # Capture before state
        before = {
            r["id"]: r["supplier"]
            for r in backfill_db.execute(
                "SELECT id, supplier FROM quotations ORDER BY id"
            ).fetchall()
        }

        MIGRATIONS[3](backfill_db)

        # Verify after state matches
        after = {
            r["id"]: r["supplier"]
            for r in backfill_db.execute(
                "SELECT id, supplier FROM quotations ORDER BY id"
            ).fetchall()
        }
        assert before == after


# =============================================================================
# TestBackfillIdempotency
# =============================================================================

class TestBackfillIdempotency:
    """Running the backfill migration twice must produce identical results."""

    def _capture_snapshot(self, conn):
        """Return a dict of all supplier + quotation state for comparison."""
        return {
            "suppliers": [
                dict(r) for r in conn.execute(
                    "SELECT id, canonical_name, display_name, status, notes"
                    " FROM suppliers ORDER BY id"
                ).fetchall()
            ],
            "audit_log": [
                dict(r) for r in conn.execute(
                    "SELECT id, supplier_id, action, actor"
                    " FROM supplier_audit_log ORDER BY id"
                ).fetchall()
            ],
            "quotations": [
                dict(r) for r in conn.execute(
                    "SELECT id, filename, supplier, normalized_supplier_name, supplier_id"
                    " FROM quotations ORDER BY id"
                ).fetchall()
            ],
        }

    def test_backfill_twice_no_duplicate_suppliers(self, backfill_db):
        """Running backfill twice does not create duplicate suppliers."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)
        snapshot1 = self._capture_snapshot(backfill_db)

        # Run again
        MIGRATIONS[3](backfill_db)
        snapshot2 = self._capture_snapshot(backfill_db)

        assert snapshot1 == snapshot2, (
            "Second backfill run modified the database state"
        )

    def test_backfill_twice_no_double_audit_entries(self, backfill_db):
        """Running backfill twice does not create duplicate audit log entries."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)
        count1 = backfill_db.execute(
            "SELECT COUNT(*) AS n FROM supplier_audit_log"
        ).fetchone()["n"]

        MIGRATIONS[3](backfill_db)
        count2 = backfill_db.execute(
            "SELECT COUNT(*) AS n FROM supplier_audit_log"
        ).fetchone()["n"]

        assert count1 == count2, (
            f"Audit log grew from {count1} to {count2} on second run"
        )

    def test_backfill_twice_quotation_links_unchanged(self, backfill_db):
        """Quotation supplier_id and normalized_supplier_name don't change."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)
        expected = {
            r["id"]: (r["normalized_supplier_name"], r["supplier_id"])
            for r in backfill_db.execute(
                "SELECT id, normalized_supplier_name, supplier_id FROM quotations"
            ).fetchall()
        }

        MIGRATIONS[3](backfill_db)
        actual = {
            r["id"]: (r["normalized_supplier_name"], r["supplier_id"])
            for r in backfill_db.execute(
                "SELECT id, normalized_supplier_name, supplier_id FROM quotations"
            ).fetchall()
        }

        assert expected == actual


# =============================================================================
# TestQuotationRegression — confirm supplier column is never modified
# =============================================================================

class TestQuotationRegression:
    """Verify that no operation in the suppliers module touches ``quotations.supplier``.

    This regression test ensures the original ``supplier`` TEXT column is
    preserved exactly as-is through all backfill operations.
    """

    def test_supplier_column_preserved_after_backfill(self, backfill_db):
        """The ``supplier`` column value is identical before and after backfill."""
        from backend.db import MIGRATIONS

        originals = {
            r["id"]: r["supplier"]
            for r in backfill_db.execute(
                "SELECT id, supplier FROM quotations ORDER BY id"
            ).fetchall()
        }

        MIGRATIONS[3](backfill_db)

        for r in backfill_db.execute(
            "SELECT id, supplier FROM quotations ORDER BY id"
        ).fetchall():
            assert r["supplier"] == originals[r["id"]], (
                f"Quotation {r['id']}: supplier column changed from "
                f"{originals[r['id']]!r} to {r['supplier']!r}"
            )

    def test_supplier_column_preserved_across_multiple_backfills(self, backfill_db):
        """Multiple backfill runs still preserve the supplier column."""
        from backend.db import MIGRATIONS

        originals = {
            r["id"]: r["supplier"]
            for r in backfill_db.execute(
                "SELECT id, supplier FROM quotations ORDER BY id"
            ).fetchall()
        }

        MIGRATIONS[3](backfill_db)
        MIGRATIONS[3](backfill_db)
        MIGRATIONS[3](backfill_db)

        for r in backfill_db.execute(
            "SELECT id, supplier FROM quotations ORDER BY id"
        ).fetchall():
            assert r["supplier"] == originals[r["id"]], (
                f"Quotation {r['id']}: supplier column changed after multiple backfills"
            )

    def test_normalize_name_never_writes(self, backfill_db):
        """Calling normalize_name directly has no side effects on the DB."""
        from backend.db import MIGRATIONS

        MIGRATIONS[3](backfill_db)
        before_count = backfill_db.execute(
            "SELECT COUNT(*) AS n FROM suppliers"
        ).fetchone()["n"]

        # normalize_name is a pure function — these calls do nothing to the DB
        normalize_name("  Some Random Name  ")
        normalize_name("Another Test")
        normalize_name(None)

        after_count = backfill_db.execute(
            "SELECT COUNT(*) AS n FROM suppliers"
        ).fetchone()["n"]
        assert before_count == after_count
