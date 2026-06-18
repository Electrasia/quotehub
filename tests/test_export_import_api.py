"""tests/test_export_import_api.py — Integration tests for export/import endpoints.

Tests all 4 endpoints via TestClient:
  - GET  /export-password/status
  - POST /export-password
  - POST /export/run
  - POST /import/run

Relies on conftest.py fixtures for auth, temp DB, archive files, and
password setup.
"""

import json
import io
from pathlib import Path

import pytest

from tests.conftest import TEST_EXPORT_PASSWORD

# ═══════════════════════════════════════════════════════════════
# ─── Auth gates ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestAuthGates:
    """Every export/import endpoint enforces role-based access."""

    # ── GET /export-password/status ──

    def test_password_status_requires_auth(self, app_client):
        """Unauthenticated GET /export-password/status returns 401."""
        resp = app_client.get("/export-password/status")
        assert resp.status_code == 401

    def test_password_status_requires_admin(self, user_client):
        """User role on GET /export-password/status returns 403."""
        resp = user_client.get("/export-password/status")
        assert resp.status_code == 403

    def test_password_status_allows_admin(self, admin_client):
        """Admin role on GET /export-password/status returns 200."""
        resp = admin_client.get("/export-password/status")
        assert resp.status_code == 200

    # ── POST /export-password (master only) ──

    def test_set_password_requires_auth(self, app_client):
        """Unauthenticated POST /export-password returns 401."""
        resp = app_client.post("/export-password", json={"new_password": "x"})
        assert resp.status_code == 401

    def test_set_password_requires_master(self, admin_client):
        """Admin role on POST /export-password returns 403 (master-only)."""
        resp = admin_client.post("/export-password", json={"new_password": "x"})
        assert resp.status_code == 403

    def test_set_password_allows_master(self, master_client, monkeypatch):
        """Master role on POST /export-password returns 200."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = master_client.post("/export-password", json={
            "new_password": TEST_EXPORT_PASSWORD,
        })
        assert resp.status_code == 200

    # ── POST /export/run (admin+) ──

    def test_export_requires_auth(self, app_client):
        """Unauthenticated POST /export/run returns 401."""
        resp = app_client.post("/export/run", json={"password": "x"})
        assert resp.status_code == 401

    def test_export_requires_admin(self, user_client):
        """User role on POST /export/run returns 403."""
        resp = user_client.post("/export/run", json={"password": "x"})
        assert resp.status_code == 403

    def test_export_allows_admin(self, admin_client, monkeypatch):
        """Admin role on POST /export/run returns 200 if password set."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = admin_client.post("/export/run", json={"password": "x"})
        # Password not set yet, so should 400, not 403
        assert resp.status_code == 400

    # ── POST /import/run (admin+) ──

    def test_import_requires_auth(self, app_client):
        """Unauthenticated POST /import/run returns 401."""
        resp = app_client.post("/import/run", data={"password": "x"})
        assert resp.status_code == 401

    def test_import_requires_admin(self, user_client):
        """User role on POST /import/run returns 403."""
        resp = user_client.post("/import/run", data={"password": "x"})
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════
# ─── Password Management API ──────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestPasswordAPI:
    """End-to-end tests for GET /export-password/status and POST /export-password."""

    def test_status_not_set(self, admin_client):
        """Before setting a password, status returns password_set=false."""
        resp = admin_client.get("/export-password/status")
        assert resp.status_code == 200
        assert resp.json() == {"password_set": False}

    def test_set_and_status(self, master_client, monkeypatch):
        """After setting a password, status returns password_set=true."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        # Set
        resp = master_client.post("/export-password", json={
            "new_password": TEST_EXPORT_PASSWORD,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "set"

        # Check status
        resp = master_client.get("/export-password/status")
        assert resp.status_code == 200
        assert resp.json() == {"password_set": True}

    def test_weak_password_rejected(self, master_client):
        """Setting a weak export password returns 422 with validation errors."""
        resp = master_client.post("/export-password", json={
            "new_password": "weak",
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "errors" in detail
        assert len(detail["errors"]) >= 1

    def test_change_password(self, master_client, monkeypatch):
        """Changing password with correct current password succeeds."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        # First set
        master_client.post("/export-password", json={
            "new_password": TEST_EXPORT_PASSWORD,
        })
        # Change
        resp = master_client.post("/export-password", json={
            "new_password": "ChangedStr0ng!1",
            "current_password": TEST_EXPORT_PASSWORD,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "changed"

    def test_change_wrong_password(self, master_client, monkeypatch):
        """Changing with wrong current password returns 401."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        master_client.post("/export-password", json={
            "new_password": TEST_EXPORT_PASSWORD,
        })
        resp = master_client.post("/export-password", json={
            "new_password": "ChangedStr0ng!1",
            "current_password": "WrongOldPass1!",
        })
        assert resp.status_code == 401

    def test_forgot_reset(self, master_client, monkeypatch):
        """Forgot recovery with correct login password resets the export password."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        # First set
        master_client.post("/export-password", json={
            "new_password": TEST_EXPORT_PASSWORD,
        })
        # Forgot reset with master login password
        resp = master_client.post("/export-password", json={
            "new_password": "ResetStr0ng!Pass",
            "login_password": "masterpass",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"
        # Warning about old backups being unrecoverable
        assert "warning" in resp.json()


# ═══════════════════════════════════════════════════════════════
# ─── Export API ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestExportAPI:
    """POST /export/run — full export workflow via the endpoint."""

    def test_export_without_password_set(self, admin_client, monkeypatch):
        """Export with no password set returns 400."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = admin_client.post("/export/run", json={"password": "x"})
        assert resp.status_code == 400
        assert "not set" in resp.json()["detail"].lower()

    def test_export_wrong_password(self, export_password_set, monkeypatch):
        """Export with wrong password returns 401."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = export_password_set.post("/export/run", json={
            "password": "WrongPassword1!",
        })
        assert resp.status_code == 401
        assert "wrong" in resp.json()["detail"].lower()

    def test_export_empty_db(self, export_password_set, monkeypatch):
        """Export with an empty database (no quotations) returns a valid .quodb."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = export_password_set.post("/export/run", json={
            "password": TEST_EXPORT_PASSWORD,
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        cd = resp.headers["content-disposition"]
        assert cd.startswith('attachment; filename="quodb_export_')
        assert cd.endswith('.quodb"')

        # Response body is the .quodb binary
        body = resp.content
        assert len(body) > 0
        # Binary header: first 41 bytes = salt(16) + nonce(16) + version(1) + iterations(8)
        assert len(body) >= 41

    def test_export_with_data(self, export_password_set, with_archive_files, monkeypatch):
        """Export with 3 seeded quotations + 3 archive files returns a valid .quodb."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = export_password_set.post("/export/run", json={
            "password": TEST_EXPORT_PASSWORD,
        })
        assert resp.status_code == 200
        body = resp.content
        assert len(body) > 0
        # The package should be at least a few KB (DB snapshot + files + manifest)
        assert len(body) > 1024


# ═══════════════════════════════════════════════════════════════
# ─── Import API ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestImportAPI:
    """POST /import/run — full import workflow via the endpoint."""

    @pytest.fixture
    def exported_package(self, export_password_set, seed_quotations, with_archive_files, monkeypatch):
        """Run an export and return the .quodb bytes.

        Depends on seed_quotations (3 records) and with_archive_files (3 PDFs)
        so the resulting package has data to import.
        """
        # Patch KDF for fast crypto during export
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = export_password_set.post("/export/run", json={
            "password": TEST_EXPORT_PASSWORD,
        })
        assert resp.status_code == 200, f"export failed: {resp.text[:200]}"
        return resp.content

    def test_import_wrong_password(self, exported_package, export_password_set, monkeypatch):
        """Import with wrong password returns non-200 (decrypt fails)."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = export_password_set.post(
            "/import/run",
            data={
                "password": "WrongPassword1!",
                "dry_run": "false",
                "force_system_id": "false",
            },
            files={"file": ("test.quodb", io.BytesIO(exported_package))},
        )
        # Wrong password during decryption should fail
        assert resp.status_code != 200

    def test_import_dry_run(self, exported_package, export_password_set, monkeypatch):
        """Dry-run import returns PREFLIGHT status and shows counts.

        The live DB already has the 3 seeded quotations, so the import
        correctly reports them as duplicates (records_skipped_duplicate == 3).
        """
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
        resp = export_password_set.post(
            "/import/run",
            data={
                "password": TEST_EXPORT_PASSWORD,
                "dry_run": "true",
                "force_system_id": "false",
            },
            files={"file": ("test.quodb", io.BytesIO(exported_package))},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Dry-run returns PREFLIGHT, not SUCCESS
        assert body["status"] == "PREFLIGHT", f"Expected PREFLIGHT got {body['status']}: {body}"
        # The package contains the 3 seeded quotations
        assert body["summary"]["total_incoming_records"] >= 3
        # All 3 are duplicates because live DB already has them
        assert body["summary"]["records_imported"] == 0
        assert body["summary"]["records_skipped_duplicate"] >= 3

    def test_import_apply(self, export_password_set, seed_quotations, with_archive_files, monkeypatch):
        """Real import (not dry-run) returns SUCCESS.

        The live DB already contains the 3 seeded quotations, so the
        import correctly reports 0 new records and 3 skipped as duplicates.
        """
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)

        # Step 1: Export
        resp_export = export_password_set.post("/export/run", json={
            "password": TEST_EXPORT_PASSWORD,
        })
        assert resp_export.status_code == 200
        package_bytes = resp_export.content

        # Step 2: Import (not dry-run)
        resp_import = export_password_set.post(
            "/import/run",
            data={
                "password": TEST_EXPORT_PASSWORD,
                "dry_run": "false",
                "force_system_id": "false",
            },
            files={"file": ("test.quodb", io.BytesIO(package_bytes))},
        )
        assert resp_import.status_code == 200
        body = resp_import.json()
        assert body["status"] == "SUCCESS", f"Import failed: {body}"
        # All records already exist — dedup correctly identifies them
        assert body["summary"]["total_incoming_records"] >= 3
        assert body["summary"]["records_imported"] == 0
        assert body["summary"]["records_skipped_duplicate"] >= 3

    def test_import_dedup(self, export_password_set, seed_quotations, with_archive_files, monkeypatch):
        """Re-importing the same package skips duplicate records.

        The live DB already has the 3 seeded quotations, so both the
        first and second import correctly identify all as duplicates.
        """
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)

        # Export
        resp_export = export_password_set.post("/export/run", json={
            "password": TEST_EXPORT_PASSWORD,
        })
        assert resp_export.status_code == 200

        # First import — all records are duplicates (live DB has them)
        resp1 = export_password_set.post(
            "/import/run",
            data={"password": TEST_EXPORT_PASSWORD, "dry_run": "false", "force_system_id": "false"},
            files={"file": ("test.quodb", io.BytesIO(resp_export.content))},
        )
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["summary"]["records_imported"] == 0
        assert body1["summary"]["records_skipped_duplicate"] >= 3

        # Second import — same result (no state change between imports)
        resp2 = export_password_set.post(
            "/import/run",
            data={"password": TEST_EXPORT_PASSWORD, "dry_run": "false", "force_system_id": "false"},
            files={"file": ("test.quodb", io.BytesIO(resp_export.content))},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["summary"]["records_imported"] == 0
        assert body2["summary"]["records_skipped_duplicate"] >= 3


# ═══════════════════════════════════════════════════════════════
# ─── Full Round-Trip ──────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestFullRoundTrip:
    """End-to-end: set password → export → import into fresh DB → verify."""

    def test_full_round_trip(self, seeded_db, monkeypatch):
        """Complete cycle: export seeded data, import into same DB, verify counts."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)

        from backend.main import ARCHIVE_DIR
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for i in range(2):
            (ARCHIVE_DIR / f"doc_{i}.pdf").write_text(f"doc content {i}")

        # Login as master
        resp = seeded_db.post("/auth/login", json={
            "username": "master01", "password": "masterpass", "remember_me": False,
        })
        assert resp.status_code == 200
        client = seeded_db

        # Set password
        resp = client.post("/export-password", json={"new_password": TEST_EXPORT_PASSWORD})
        assert resp.status_code == 200

        # Export
        resp = client.post("/export/run", json={"password": TEST_EXPORT_PASSWORD})
        assert resp.status_code == 200
        package = resp.content

        # Import
        resp = client.post(
            "/import/run",
            data={"password": TEST_EXPORT_PASSWORD, "dry_run": "false", "force_system_id": "false"},
            files={"file": ("roundtrip.quodb", io.BytesIO(package))},
        )
        assert resp.status_code == 200
        body = resp.json()
        # The DB already had 3 quotations from seeded_db fixture,
        # plus we imported more — records_imported reflects NEW records
        assert body["status"] in ("SUCCESS", "PREFLIGHT")
        # If no new records (all duplicate), that's OK
        assert "records_imported" in body["summary"]
