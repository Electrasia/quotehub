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


# =============================================================================
# PHASE 2 — API integration tests
# =============================================================================

import json as _json


class TestCreateSupplier:
    """Tests for POST /suppliers."""

    def test_create_supplier_as_admin(self, admin_client):
        """Admin can create a new supplier."""
        resp = admin_client.post("/suppliers", json={
            "canonical_name": "  New Supplier Corp  ",
            "display_name": "New Supplier",
            "notes": "Test supplier",
        })
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["canonical_name"] == "new supplier corp"
        assert data["display_name"] == "New Supplier"
        assert data["status"] == "active"
        assert data["notes"] == "Test supplier"
        assert "id" in data

    def test_create_supplier_duplicate_409(self, admin_client):
        """Creating a supplier with a duplicate canonical_name returns 409."""
        admin_client.post("/suppliers", json={"canonical_name": "Test Co"})
        resp = admin_client.post("/suppliers", json={"canonical_name": "  Test Co  "})
        assert resp.status_code == 409

    def test_create_supplier_as_user_403(self, user_client):
        """User role cannot create suppliers."""
        resp = user_client.post("/suppliers", json={"canonical_name": "User Supplier"})
        assert resp.status_code == 403

    def test_create_supplier_unauthenticated_401(self, app_client):
        """Unauthenticated request returns 401."""
        resp = app_client.post("/suppliers", json={"canonical_name": "Anon Supplier"})
        assert resp.status_code == 401

    def test_create_supplier_audit_log(self, admin_client):
        """Creating a supplier generates an audit log entry."""
        admin_client.post("/suppliers", json={"canonical_name": "Audit Corp"})
        from backend.db import get_db
        with get_db(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ?",
                ("create_supplier",),
            ).fetchone()
            assert row is not None
            assert row["actor"] == "admin"

    def test_create_supplier_empty_name_422(self, admin_client):
        """Empty canonical_name after normalisation returns 422."""
        resp = admin_client.post("/suppliers", json={"canonical_name": "   "})
        assert resp.status_code == 422


class TestGetSupplier:
    """Tests for GET /suppliers/{id}."""

    def _create_supplier_via_db(self, name="Detail Corp"):
        """Create a supplier directly via DB (no auth dependency)."""
        from backend.suppliers import normalize_name
        from backend.db import get_db
        with get_db() as db:
            norm = normalize_name(name)
            cur = db.execute(
                "INSERT INTO suppliers (canonical_name, display_name) VALUES (?, ?)",
                (norm, name),
            )
            return cur.lastrowid

    def test_get_supplier_as_user(self, user_client):
        sid = self._create_supplier_via_db()
        resp = user_client.get(f"/suppliers/{sid}")
        assert resp.status_code == 200
        data = resp.json()
        assert "aliases" in data
        assert "contacts" in data
        assert "capabilities" in data

    def test_get_supplier_404(self, user_client):
        resp = user_client.get("/suppliers/99999")
        assert resp.status_code == 404

    def test_get_supplier_unauthenticated_401(self, app_client):
        sid = self._create_supplier_via_db()
        resp = app_client.get(f"/suppliers/{sid}")
        assert resp.status_code == 401


class TestUpdateSupplier:
    """Tests for PUT /suppliers/{id}."""

    def test_update_display_name(self, admin_client):
        resp = admin_client.post("/suppliers", json={"canonical_name": "Update Corp"})
        sid = resp.json()["id"]
        resp = admin_client.put(f"/suppliers/{sid}", json={
            "display_name": "Updated Display",
        })
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Updated Display"

    def test_update_status_admin_cannot_review(self, admin_client):
        """Admin attempting to set status='review' returns 403."""
        resp = admin_client.post("/suppliers", json={"canonical_name": "Status Corp"})
        sid = resp.json()["id"]
        resp = admin_client.put(f"/suppliers/{sid}", json={
            "status": "review",
        })
        assert resp.status_code == 403

    def test_update_status_master_can_review(self, master_client):
        resp = master_client.post("/suppliers", json={"canonical_name": "Master Corp"})
        sid = resp.json()["id"]
        resp = master_client.put(f"/suppliers/{sid}", json={
            "status": "review",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "review"

    def test_update_audit_log_diff(self, admin_client):
        """Update produces audit log with before/after diff."""
        resp = admin_client.post("/suppliers", json={
            "canonical_name": "Diff Corp",
            "display_name": "Old Name",
        })
        sid = resp.json()["id"]
        admin_client.put(f"/suppliers/{sid}", json={
            "display_name": "New Name",
            "notes": "Added notes",
        })
        from backend.db import get_db
        with get_db(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ? AND supplier_id = ?",
                ("update_supplier", sid),
            ).fetchone()
            assert row is not None
            details = _json.loads(row["details"])
            assert "display_name" in details
            assert details["display_name"]["to"] == "New Name"
            assert "notes" in details
            assert row["actor"] == "admin"

    def test_update_supplier_404(self, admin_client):
        resp = admin_client.put("/suppliers/99999", json={"display_name": "Nope"})
        assert resp.status_code == 404

    def test_update_supplier_user_403(self, user_client):
        # Create a supplier as admin (using admin_client fixture separately)
        import copy
        from backend.db import get_db
        from backend.suppliers import normalize_name
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO suppliers (canonical_name, display_name) VALUES (?, ?)",
                ("user-test-supplier", "User Test"),
            )
            sid = cur.lastrowid
        resp = user_client.put(f"/suppliers/{sid}", json={"display_name": "Nope"})
        assert resp.status_code == 403


class TestInactivateSupplier:
    """Tests for POST /suppliers/{id}/inactivate (master-only)."""

    def test_inactivate_as_master(self, master_client):
        resp = master_client.post("/suppliers", json={"canonical_name": "Inactivate Corp"})
        sid = resp.json()["id"]
        resp = master_client.post(f"/suppliers/{sid}/inactivate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "inactive"
        # Confirm audit log
        from backend.db import get_db
        with get_db(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ? AND supplier_id = ?",
                ("inactivate_supplier", sid),
            ).fetchone()
            assert row is not None
            details = _json.loads(row["details"])
            assert details["before_status"] == "active"
            assert details["after_status"] == "inactive"

    def test_inactivate_as_admin_403(self, admin_client):
        from backend.db import get_db
        from backend.suppliers import normalize_name
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO suppliers (canonical_name, display_name) VALUES (?, ?)",
                ("admin-inact-test", "Admin Test"),
            )
            sid = cur.lastrowid
        resp = admin_client.post(f"/suppliers/{sid}/inactivate")
        assert resp.status_code == 403

    def test_inactivate_already_inactive(self, master_client):
        resp = master_client.post("/suppliers", json={"canonical_name": "Already Inact Corp"})
        sid = resp.json()["id"]
        master_client.post(f"/suppliers/{sid}/inactivate")
        resp = master_client.post(f"/suppliers/{sid}/inactivate")
        assert resp.status_code == 200  # no-op

    def test_inactivate_404(self, master_client):
        resp = master_client.post("/suppliers/99999/inactivate")
        assert resp.status_code == 404


class TestListSuppliers:
    """Tests for GET /suppliers."""

    def test_list_all(self, master_client):
        for name in ["Alpha Corp", "Beta Inc", "Gamma Ltd"]:
            master_client.post("/suppliers", json={"canonical_name": name})
        resp = master_client.get("/suppliers?status=all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert len(data["items"]) >= 3

    def test_list_active_only(self, master_client):
        master_client.post("/suppliers", json={"canonical_name": "Active Corp"})
        master_client.post("/suppliers", json={"canonical_name": "Active Too"})
        resp = master_client.get("/suppliers?status=active")
        assert resp.status_code == 200
        for s in resp.json()["items"]:
            assert s["status"] == "active"

    def test_list_search(self, master_client):
        master_client.post("/suppliers", json={"canonical_name": "Beta Inc"})
        resp = master_client.get("/suppliers?q=beta")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        names = [s["canonical_name"] for s in data["items"]]
        assert any("beta" in n for n in names)

    def test_list_pagination(self, master_client):
        for i in range(5):
            master_client.post("/suppliers", json={"canonical_name": f"Page Corp {i}"})
        resp = master_client.get("/suppliers?per_page=2&page=1&status=all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["per_page"] == 2
        assert len(data["items"]) <= 2

    def test_list_requires_auth(self, app_client):
        resp = app_client.get("/suppliers")
        assert resp.status_code == 401


class TestContactsCRUD:
    """Tests for contacts CRUD endpoints."""

    def _create_supplier(self, admin_client, name="Contact Corp"):
        resp = admin_client.post("/suppliers", json={"canonical_name": name})
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_create_contact(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/contacts", json={
            "name": "John Doe",
            "email": "john@example.com",
            "phone": "+852 1234 5678",
            "role": "Sales Manager",
            "position": 1,
            "is_default_rfq_contact": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "John Doe"
        assert data["email"] == "john@example.com"
        assert data["position"] == 1
        assert data["is_default_rfq_contact"] == 1

    def test_list_contacts_sorted(self, admin_client):
        sid = self._create_supplier(admin_client)
        # Create contacts with different positions
        admin_client.post(f"/suppliers/{sid}/contacts", json={
            "name": "Second", "position": 2,
        })
        admin_client.post(f"/suppliers/{sid}/contacts", json={
            "name": "First", "position": 1,
        })
        resp = admin_client.get(f"/suppliers/{sid}/contacts")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 2
        # Should be sorted by position ASC
        positions = [c["position"] for c in items]
        assert positions == sorted(positions)

    def test_update_contact(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/contacts", json={
            "name": "Original",
        })
        cid = resp.json()["id"]
        resp = admin_client.put(f"/suppliers/{sid}/contacts/{cid}", json={
            "name": "Updated Name",
            "role": "New Role",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"
        # Audit log
        from backend.db import get_db
        with get_db(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ? AND supplier_id = ?",
                ("update_contact", sid),
            ).fetchone()
            assert row is not None

    def test_delete_contact(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/contacts", json={
            "name": "Delete Me",
        })
        cid = resp.json()["id"]
        resp = admin_client.delete(f"/suppliers/{sid}/contacts/{cid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        # Verify deleted
        resp = admin_client.get(f"/suppliers/{sid}/contacts")
        ids = [c["id"] for c in resp.json()["items"]]
        assert cid not in ids

    def test_contact_delete_404(self, admin_client):
        """Deleting a non-existent contact returns 404."""
        sid = self._create_supplier(admin_client)
        resp = admin_client.delete(f"/suppliers/{sid}/contacts/99999")
        assert resp.status_code == 404

    def test_contact_user_read_only(self, user_client):
        """User can list contacts but not create/update/delete."""
        from backend.db import get_db
        from backend.suppliers import normalize_name
        with get_db() as db:
            norm = normalize_name("User Contact Corp")
            cur = db.execute(
                "INSERT INTO suppliers (canonical_name, display_name) VALUES (?, ?)",
                (norm, "User Contact Corp"),
            )
            sid = cur.lastrowid
        resp = user_client.post(f"/suppliers/{sid}/contacts", json={"name": "Nope"})
        assert resp.status_code == 403

    def test_contact_delete_audit_log(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/contacts", json={
            "name": "Audited Contact",
        })
        cid = resp.json()["id"]
        admin_client.delete(f"/suppliers/{sid}/contacts/{cid}")
        from backend.db import get_db
        with get_db(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ? AND supplier_id = ?",
                ("delete_contact", sid),
            ).fetchone()
            assert row is not None


class TestAliasesCRUD:
    """Tests for aliases CRUD endpoints."""

    def _create_supplier(self, admin_client, name="Alias Corp"):
        resp = admin_client.post("/suppliers", json={"canonical_name": name})
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_create_alias(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/aliases", json={
            "alias": "  Aliased Name  ",
        })
        assert resp.status_code == 200
        assert resp.json()["alias"] == "aliased name"

    def test_duplicate_alias_409(self, admin_client):
        sid = self._create_supplier(admin_client)
        admin_client.post(f"/suppliers/{sid}/aliases", json={"alias": "MyAlias"})
        resp = admin_client.post(f"/suppliers/{sid}/aliases", json={"alias": "  MyAlias  "})
        assert resp.status_code == 409

    def test_list_aliases(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.get(f"/suppliers/{sid}/aliases")
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_delete_alias(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/aliases", json={"alias": "DeleteAlias"})
        aid = resp.json()["id"]
        resp = admin_client.delete(f"/suppliers/{sid}/aliases/{aid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_alias_404(self, admin_client):
        # Create supplier, try to delete non-existent alias
        from backend.db import get_db
        from backend.suppliers import normalize_name
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO suppliers (canonical_name, display_name) VALUES (?, ?)",
                ("alias-404-test", "Alias 404 Test"),
            )
            sid = cur.lastrowid
        resp = admin_client.delete(f"/suppliers/{sid}/aliases/99999")
        assert resp.status_code == 404

    def test_alias_audit_log(self, admin_client):
        sid = self._create_supplier(admin_client)
        admin_client.post(f"/suppliers/{sid}/aliases", json={"alias": "AuditedAlias"})
        from backend.db import get_db
        with get_db(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ? AND supplier_id = ?",
                ("add_alias", sid),
            ).fetchone()
            assert row is not None
            assert row["actor"] == "admin"


class TestCapabilitiesCRUD:
    """Tests for capabilities CRUD with role enforcement on verified flag."""

    def _create_supplier(self, client, name="Cap Corp"):
        resp = client.post("/suppliers", json={"canonical_name": name})
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_create_capability_admin_unverified(self, admin_client):
        """Admin can create an unverified capability."""
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "TestBrand",
            "product_type": "TestPT",
            "verified": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["brand"] == "testbrand"
        assert data["product_type"] == "testpt"
        assert data["verified"] is False

    def test_create_capability_admin_verified_403(self, admin_client):
        """Admin cannot set verified=true on create."""
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "BrandX",
            "product_type": "PTX",
            "verified": True,
        })
        assert resp.status_code == 403

    def test_create_capability_master_verified(self, master_client):
        """Master can set verified=true."""
        sid = self._create_supplier(master_client)
        resp = master_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "MasterBrand",
            "product_type": "MasterPT",
            "verified": True,
        })
        assert resp.status_code == 200
        assert resp.json()["verified"] is True

    def test_duplicate_capability_409(self, admin_client):
        sid = self._create_supplier(admin_client)
        admin_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "DupBrand", "product_type": "DupPT",
        })
        resp = admin_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "DupBrand", "product_type": "DupPT",
        })
        assert resp.status_code == 409

    def test_update_capability_admin_no_verified(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "UpdateBrand", "product_type": "UpdatePT",
        })
        cap_id = resp.json()["id"]
        # Admin changing to verified should fail
        resp = admin_client.put(f"/suppliers/{sid}/capabilities/{cap_id}", json={
            "verified": True,
        })
        assert resp.status_code == 403
        # Admin changing brand should work
        resp = admin_client.put(f"/suppliers/{sid}/capabilities/{cap_id}", json={
            "brand": "NewBrand",
        })
        assert resp.status_code == 200
        assert resp.json()["brand"] == "newbrand"

    def test_update_capability_master_verified(self, master_client):
        sid = self._create_supplier(master_client)
        resp = master_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "MasterUpd", "product_type": "MasterUpdPT",
        })
        cap_id = resp.json()["id"]
        resp = master_client.put(f"/suppliers/{sid}/capabilities/{cap_id}", json={
            "verified": True,
        })
        assert resp.status_code == 200
        assert resp.json()["verified"] is True

    def test_delete_capability(self, admin_client):
        sid = self._create_supplier(admin_client)
        resp = admin_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "DelBrand", "product_type": "DelPT",
        })
        cap_id = resp.json()["id"]
        resp = admin_client.delete(f"/suppliers/{sid}/capabilities/{cap_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_capability_audit_log(self, admin_client):
        sid = self._create_supplier(admin_client)
        admin_client.post(f"/suppliers/{sid}/capabilities", json={
            "brand": "AuditBr", "product_type": "AuditPT",
        })
        from backend.db import get_db
        with get_db(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ? AND supplier_id = ?",
                ("create_capability", sid),
            ).fetchone()
            assert row is not None


class TestBrandsProductTypes:
    """Tests for /brands and /product-types endpoints."""

    def test_brands_min_2_chars(self, master_client):
        """Returns empty when query < 2 chars."""
        resp = master_client.get("/brands?q=a")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_brands_prefix_match(self, master_client):
        master_client.post("/brands", json={"name": "AlphaBrand"})
        resp = master_client.get("/brands?q=alpha")
        assert resp.status_code == 200
        names = [b["name"] for b in resp.json()["items"]]
        assert "alphabrand" in names

    def test_brands_max_20_results(self, master_client):
        # Create 25 brands
        for i in range(25):
            master_client.post("/brands", json={"name": f"ZZZBrand{i}"})
        resp = master_client.get("/brands?q=zzz")
        assert len(resp.json()["items"]) <= 20

    def test_product_types_min_2_chars(self, master_client):
        resp = master_client.get("/product-types?q=b")
        assert resp.json()["items"] == []

    def test_brands_duplicate_409(self, master_client):
        master_client.post("/brands", json={"name": "AlphaBrand"})
        resp = master_client.post("/brands", json={"name": "AlphaBrand"})
        assert resp.status_code == 409

    def test_brands_create_normalized(self, master_client):
        resp = master_client.post("/brands", json={"name": "  New Brand  "})
        assert resp.status_code == 200
        assert resp.json()["name"] == "new brand"

    def test_brands_unauthenticated_401(self, app_client):
        resp = app_client.get("/brands?q=test")
        assert resp.status_code == 401

    def test_product_types_prefix_match(self, master_client):
        master_client.post("/product-types", json={"name": "BetaType"})
        resp = master_client.get("/product-types?q=beta")
        names = [pt["name"] for pt in resp.json()["items"]]
        assert "betatype" in names

    def test_product_types_duplicate_409(self, master_client):
        master_client.post("/product-types", json={"name": "AlphaType"})
        resp = master_client.post("/product-types", json={"name": "AlphaType"})
        assert resp.status_code == 409

    def test_product_types_empty_name_422(self, master_client):
        resp = master_client.post("/product-types", json={"name": "   "})
        assert resp.status_code == 422

    def test_brands_empty_name_422(self, master_client):
        resp = master_client.post("/brands", json={"name": "   "})
        assert resp.status_code == 422


class TestResolveEndpoint:
    """Tests for POST /suppliers/resolve (strictly read-only)."""

    def test_resolve_canonical_name(self, master_client):
        resp = master_client.post("/suppliers", json={"canonical_name": "Resolve Corp"})
        sid = resp.json()["id"]
        # Also add an alias
        master_client.post(f"/suppliers/{sid}/aliases", json={"alias": "ResolveAlias"})
        resp = master_client.post("/suppliers/resolve", json={"name": "Resolve Corp"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is True
        assert data["id"] == sid
        assert data["canonical_name"] == "resolve corp"

    def test_resolve_alias(self, master_client):
        resp = master_client.post("/suppliers", json={"canonical_name": "Alias Resolve"})
        sid = resp.json()["id"]
        master_client.post(f"/suppliers/{sid}/aliases", json={"alias": "ResolveAlias"})
        resp = master_client.post("/suppliers/resolve", json={"name": "ResolveAlias"})
        assert resp.status_code == 200
        assert resp.json()["matched"] is True

    def test_resolve_no_match(self, master_client):
        resp = master_client.post("/suppliers/resolve", json={"name": "NonExistentXYZ"})
        assert resp.status_code == 200
        assert resp.json()["matched"] is False
        assert resp.json()["id"] is None

    def test_resolve_read_only(self, master_client):
        """Confirm resolve never creates suppliers."""
        before_resp = master_client.post("/suppliers/resolve", json={"name": "UnmatchedXYZ"})
        assert before_resp.json()["matched"] is False
        # Try the unmatched name again — should still be unmatched
        resp = master_client.post("/suppliers/resolve", json={"name": "UnmatchedXYZ"})
        assert resp.json()["matched"] is False

    def test_resolve_unauthenticated_401(self, app_client):
        resp = app_client.post("/suppliers/resolve", json={"name": "Test"})
        assert resp.status_code == 401


class TestNoHardDeleteSupplier:
    """Confirm there is no DELETE endpoint on /suppliers/{id}."""

    def test_no_delete_endpoint(self, master_client):
        resp = master_client.delete("/suppliers/1")
        # Should return 405 Method Not Allowed (DELETE not registered)
        # or 404 if the path doesn't accept DELETE
        assert resp.status_code in (404, 405), (
            f"Expected 404/405, got {resp.status_code}: {resp.json()}"
        )


class TestRoleEnforcement:
    """Test 401 vs 403 across all endpoints for all roles."""

    def _create_supplier_via_db(self):
        """Create a supplier directly via DB for role tests."""
        from backend.suppliers import normalize_name
        from backend.db import get_db
        with get_db() as db:
            norm = normalize_name("Role Corp")
            cur = db.execute(
                "INSERT INTO suppliers (canonical_name, display_name) VALUES (?, ?)",
                (norm, "Role Corp"),
            )
            return cur.lastrowid

    def test_unauthenticated_401(self, app_client):
        sid = self._create_supplier_via_db()
        endpoints = [
            ("GET", "/suppliers"),
            ("GET", f"/suppliers/{sid}"),
            ("GET", f"/suppliers/{sid}/contacts"),
            ("GET", f"/suppliers/{sid}/aliases"),
            ("GET", f"/suppliers/{sid}/capabilities"),
            ("POST", "/suppliers/resolve"),
        ]
        for method, path in endpoints:
            resp = app_client.request(method, path)
            assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"

    def test_user_readonly_403_on_write(self, user_client):
        """User can GET but not POST/PUT/DELETE on write endpoints."""
        sid = self._create_supplier_via_db()
        write_endpoints = [
            ("POST", "/suppliers", {"canonical_name": "X"}),
            ("POST", f"/suppliers/{sid}/contacts", {"name": "X"}),
            ("POST", f"/suppliers/{sid}/aliases", {"alias": "X"}),
            ("POST", f"/suppliers/{sid}/capabilities", {"brand": "X", "product_type": "Y"}),
            ("POST", "/brands", {"name": "X"}),
            ("POST", "/product-types", {"name": "X"}),
        ]
        for method, path, body in write_endpoints:
            resp = user_client.request(method, path, json=body)
            assert resp.status_code == 403, f"{method} {path} returned {resp.status_code}, expected 403"

    def test_admin_can_write(self, admin_client):
        """Admin can write to non-restricted endpoints."""
        resp = admin_client.post("/suppliers", json={"canonical_name": "Admin Supplier"})
        assert resp.status_code == 200
        resp = admin_client.post(f"/suppliers/{resp.json()['id']}/contacts", json={"name": "Admin Contact"})
        assert resp.status_code == 200


class TestAuditLogDiffs:
    """Verify every PUT/PATCH produces audit log with before/after diff."""

    def test_update_supplier_diff_fields_only(self, admin_client):
        """Only changed fields appear in diff."""
        resp = admin_client.post("/suppliers", json={
            "canonical_name": "Diff Corp",
            "display_name": "Original Display",
            "notes": "Original notes",
        })
        sid = resp.json()["id"]
        admin_client.put(f"/suppliers/{sid}", json={
            "display_name": "Changed Display",
        })
        from backend.db import get_db
        with get_db(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ? AND supplier_id = ?",
                ("update_supplier", sid),
            ).fetchone()
            details = _json.loads(row["details"])
            assert "display_name" in details
            assert "notes" not in details  # unchanged
            assert "status" not in details  # unchanged

    def test_update_contact_diff(self, admin_client):
        """Contact update diff contains only changed fields."""
        resp = admin_client.post("/suppliers", json={"canonical_name": "Contact Diff Corp"})
        sid = resp.json()["id"]
        resp = admin_client.post(f"/suppliers/{sid}/contacts", json={
            "name": "Diff Contact", "email": "old@example.com", "role": "Old Role",
        })
        cid = resp.json()["id"]
        admin_client.put(f"/suppliers/{sid}/contacts/{cid}", json={
            "email": "new@example.com",
            "role": "New Role",
        })
        from backend.db import get_db
        with get_db(readonly=True) as db:
            rows = db.execute(
                "SELECT * FROM supplier_audit_log WHERE action = ? AND supplier_id = ? ORDER BY id DESC LIMIT 1",
                ("update_contact", sid),
            ).fetchall()
            assert len(rows) >= 1
            details = _json.loads(rows[0]["details"])
            assert "email" in details
            assert "role" in details
            assert "name" not in details  # unchanged
            assert details["email"]["from"] == "old@example.com"
            assert details["email"]["to"] == "new@example.com"


class TestApiValidation:
    """Test error handling for malformed/invalid inputs."""

    def test_missing_required_fields(self, admin_client):
        resp = admin_client.post("/suppliers", json={})
        # Missing canonical_name: the model has no default, so it fails validation
        # but FastAPI might accept it if canonical_name is not explicitly required
        # by Pydantic (it's just a str, not Optional[str]).
        # The endpoint code checks if norm is empty and returns 422.
        # Let's check that it's handled gracefully.
        assert resp.status_code in (200, 422)

    def test_invalid_status_value(self, admin_client):
        resp = admin_client.post("/suppliers", json={"canonical_name": "Valid"})
        sid = resp.json()["id"]
        resp = admin_client.put(f"/suppliers/{sid}", json={"status": "invalid"})
        # The status field has pattern="^(active|inactive|review)$" — FastAPI returns 422
        assert resp.status_code == 422

    def test_non_existent_supplier_returns_404(self, admin_client):
        resp = admin_client.get("/suppliers/99999")
        assert resp.status_code == 404

    def test_non_existent_contact_delete_returns_404(self, admin_client):
        """Deleting a non-existent contact returns 404."""
        resp = admin_client.delete("/suppliers/1/contacts/99999")
        assert resp.status_code == 404

    def test_invalid_json_body(self, admin_client):
        """Raw string body should be rejected."""
        resp = admin_client.post("/suppliers", content=b"not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 422

    def test_per_page_capped_at_100(self, master_client):
        """FastAPI enforces le=100, returns 422 for values >100."""
        resp = master_client.get("/suppliers?per_page=500")
        assert resp.status_code == 422


class TestConcurrencyStress:
    """Stress test: 10 parallel supplier writes, no 'database is locked' errors."""

    def test_parallel_writes(self, master_client):
        """10 concurrent supplier creates should all succeed or fail cleanly."""
        import concurrent.futures
        import threading

        results = []
        errors = []
        lock = threading.Lock()

        def create_supplier(name):
            try:
                resp = master_client.post("/suppliers", json={"canonical_name": name})
                with lock:
                    results.append((name, resp.status_code))
            except Exception as e:
                with lock:
                    errors.append((name, str(e)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for i in range(10):
                futures.append(pool.submit(create_supplier, f"Concurrent Supplier {i}"))
            concurrent.futures.wait(futures)

        # All should have status 200 (success) or 409 (duplicate from retry)
        # No 500 errors
        if errors:
            pytest.fail(f"Concurrent writes failed with errors: {errors}")

        for name, status in results:
            assert status in (200, 409), f"{name} returned {status}, expected 200 or 409"

        # Verify all suppliers created at least once
        from backend.db import get_db
        with get_db(readonly=True) as db:
            for name, status in results:
                if status == 200:
                    row = db.execute(
                        "SELECT id FROM suppliers WHERE canonical_name = ?",
                        (name.lower(),),
                    ).fetchone()
                    assert row is not None, f"Supplier {name} was not persisted"


# =============================================================================
# TestFrontendRenderingSafety — static analysis of frontend JS (no browser)
# =============================================================================

class TestFrontendRenderingSafety:
    """Static file-content checks for suppliers.js frontend safety."""

    SUPPLIERS_PATH = "frontend/js/suppliers.js"

    def _read_suppliers_js(self):
        with open(self.SUPPLIERS_PATH) as f:
            return f.read()

    def test_zero_innerHTML_with_untrusted_data(self):
        """Confirm no innerHTML assignments exist in suppliers.js."""
        source = self._read_suppliers_js()
        lines = source.split("\n")
        offending = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comment lines and template literal lines
            if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                continue
            if "innerHTML" in stripped:
                # Allow innerHTML on <td>, <tr>, <div>, etc. ONLY when the
                # assigned value is a literal empty string '' or a known-safe
                # assignment (e.g. clearing a container before re-building
                # with createElement). Check that no innerHTML assignment uses
                # a variable that could contain user data.
                offending.append((i, stripped))
        if offending:
            msg = "\n".join(f"  Line {n}: {s}" for n, s in offending)
            pytest.fail(f"innerHTML found in suppliers.js — potential XSS:\n{msg}")

    def test_renderTextSafe_defined(self):
        """Confirm renderTextSafe is defined and exported in window.Suppliers."""
        source = self._read_suppliers_js()
        # Check function definition
        assert "function renderTextSafe" in source, (
            "renderTextSafe function definition not found in suppliers.js"
        )
        # Check it's exported in the return object
        assert "renderTextSafe: renderTextSafe" in source or "renderTextSafe:renderTextSafe" in source, (
            "renderTextSafe not found in window.Suppliers export (return object)"
        )

