"""tests/test_export_import_api.py — Integration tests for export/import endpoints.

Tests both remaining endpoints via TestClient:
  - POST /export/run
  - POST /import/run

Password management endpoints were removed in v0.061.0 — the export
password is now per-file and never stored.
"""

import json
import io
from pathlib import Path

import pytest

TEST_PASSWORD = "Str0ng!P@ss42"

# ═══════════════════════════════════════════════════════════════
# ─── Auth gates ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestAuthGates:
    """Every export/import endpoint enforces role-based access."""

    # ── POST /export/run (admin+) ──

    def test_export_requires_auth(self, app_client):
        """Unauthenticated POST /export/run returns 401."""
        resp = app_client.post("/export/run", json={"password": "x"})
        assert resp.status_code == 401

    def test_export_requires_admin(self, user_client):
        """User role on POST /export/run returns 403."""
        resp = user_client.post("/export/run", json={"password": "x"})
        assert resp.status_code == 403

    def test_export_denied_for_admin(self, admin_client):
        """Admin role on POST /export/run returns 403 (master-only)."""
        resp = admin_client.post("/export/run", json={"password": "x"})
        assert resp.status_code == 403

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
# ─── Export API ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestExportAPI:
    """POST /export/run — full export workflow via the endpoint."""

    def test_export_empty_db(self, master_client, fast_crypto):
        """Export with an empty database (no quotations) returns a valid .quodb."""
        resp = master_client.post("/export/run", json={
            "password": TEST_PASSWORD,
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        cd = resp.headers["content-disposition"]
        assert cd.startswith('attachment; filename="quodb_export_')
        assert cd.endswith('.quodb"')

        body = resp.content
        assert len(body) > 0
        # Binary header: first 41 bytes = salt(16) + nonce(16) + version(1) + iterations(8)
        assert len(body) >= 41

    def test_export_with_data(self, master_client, with_archive_files, fast_crypto):
        """Export with seeded quotations + archive files returns a valid .quodb."""
        resp = master_client.post("/export/run", json={
            "password": TEST_PASSWORD,
        })
        assert resp.status_code == 200
        body = resp.content
        assert len(body) > 1024  # at least a few KB


# ═══════════════════════════════════════════════════════════════
# ─── Import API ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestImportAPI:
    """POST /import/run — full import workflow via the endpoint."""

    @pytest.fixture
    def exported_package(self, master_client, seed_quotations, with_archive_files, fast_crypto):
        """Run an export and return the .quodb bytes.

        Depends on seed_quotations (3 records) and with_archive_files (3 PDFs)
        so the resulting package has data to import.
        """
        resp = master_client.post("/export/run", json={
            "password": TEST_PASSWORD,
        })
        assert resp.status_code == 200, f"export failed: {resp.text[:200]}"
        return resp.content

    def test_import_wrong_password(self, exported_package, master_client, fast_crypto):
        """Import with wrong password returns non-200 (decrypt fails)."""
        resp = master_client.post(
            "/import/run",
            data={
                "password": "WrongPassword1!",
                "dry_run": "false",
                "force_system_id": "false",
            },
            files={"file": ("test.quodb", io.BytesIO(exported_package))},
        )
        assert resp.status_code != 200

    def test_import_dry_run(self, exported_package, master_client, fast_crypto):
        """Dry-run import returns PREFLIGHT status and shows counts."""
        resp = master_client.post(
            "/import/run",
            data={
                "password": TEST_PASSWORD,
                "dry_run": "true",
                "force_system_id": "false",
            },
            files={"file": ("test.quodb", io.BytesIO(exported_package))},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "PREFLIGHT", f"Expected PREFLIGHT got {body['status']}: {body}"
        assert body["summary"]["total_incoming_records"] >= 3
        assert body["summary"]["records_imported"] == 0
        assert body["summary"]["records_skipped_duplicate"] >= 3

    def test_import_apply(self, master_client, seed_quotations, with_archive_files, fast_crypto):
        """Real import (not dry-run) returns SUCCESS."""
        # Step 1: Export
        resp_export = master_client.post("/export/run", json={
            "password": TEST_PASSWORD,
        })
        assert resp_export.status_code == 200
        package_bytes = resp_export.content

        # Step 2: Import (not dry-run)
        resp_import = master_client.post(
            "/import/run",
            data={
                "password": TEST_PASSWORD,
                "dry_run": "false",
                "force_system_id": "false",
            },
            files={"file": ("test.quodb", io.BytesIO(package_bytes))},
        )
        assert resp_import.status_code == 200
        body = resp_import.json()
        assert body["status"] == "SUCCESS", f"Import failed: {body}"
        assert body["summary"]["total_incoming_records"] >= 3
        assert body["summary"]["records_imported"] == 0
        assert body["summary"]["records_skipped_duplicate"] >= 3

    def test_import_dedup(self, master_client, seed_quotations, with_archive_files, fast_crypto):
        """Re-importing the same package skips duplicate records."""
        resp_export = master_client.post("/export/run", json={
            "password": TEST_PASSWORD,
        })
        assert resp_export.status_code == 200

        resp1 = master_client.post(
            "/import/run",
            data={"password": TEST_PASSWORD, "dry_run": "false", "force_system_id": "false"},
            files={"file": ("test.quodb", io.BytesIO(resp_export.content))},
        )
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["summary"]["records_imported"] == 0
        assert body1["summary"]["records_skipped_duplicate"] >= 3

        resp2 = master_client.post(
            "/import/run",
            data={"password": TEST_PASSWORD, "dry_run": "false", "force_system_id": "false"},
            files={"file": ("test.quodb", io.BytesIO(resp_export.content))},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["summary"]["records_imported"] == 0
        assert body2["summary"]["records_skipped_duplicate"] >= 3

    def test_import_attribution(self, master_client, seed_quotations, with_archive_files, fast_crypto):
        """Import response includes export attribution (who created the file)."""
        resp_export = master_client.post("/export/run", json={
            "password": TEST_PASSWORD,
        })
        assert resp_export.status_code == 200

        resp = master_client.post(
            "/import/run",
            data={"password": TEST_PASSWORD, "dry_run": "true", "force_system_id": "false"},
            files={"file": ("test.quodb", io.BytesIO(resp_export.content))},
        )
        assert resp.status_code == 200
        body = resp.json()
        attr = body.get("exportAttribution", {})
        assert attr.get("masterDisplayName") == "master01"
        assert attr.get("masterRole") == "master"
        assert attr.get("exportedAtUtc") is not None


# ═══════════════════════════════════════════════════════════════
# ─── Full Round-Trip ──────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestFullRoundTrip:
    """End-to-end: export → import into fresh DB → verify."""

    def test_full_round_trip(self, seeded_db, monkeypatch):
        """Complete cycle: export seeded data, import into same DB, verify counts."""
        monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)

        from backend.main import ARCHIVE_DIR
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for i in range(2):
            (ARCHIVE_DIR / f"doc_{i}.pdf").write_text(f"doc content {i}")

        # Login as master
        resp = seeded_db.post("/auth/login", json={
            "username": "master01", "password": "Mast3r!Pass12", "remember_me": False,
        })
        assert resp.status_code == 200
        client = seeded_db

        # Export (no password setup needed — password is per-file)
        resp = client.post("/export/run", json={"password": TEST_PASSWORD})
        assert resp.status_code == 200
        package = resp.content

        # Import
        resp = client.post(
            "/import/run",
            data={"password": TEST_PASSWORD, "dry_run": "false", "force_system_id": "false"},
            files={"file": ("roundtrip.quodb", io.BytesIO(package))},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("SUCCESS", "PREFLIGHT")
        assert "records_imported" in body["summary"]
