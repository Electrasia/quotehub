"""tests/test_export_import.py — Export and import endpoint tests.

Covers GET /export (ZIP download with SHA256 integrity) and
POST /import/upload (ZIP and JSON upload with integrity verification).

All tests use in-memory buffers — no real files are written outside the
patched temp directories provided by the app_client fixture.
"""

import hashlib
import io
import json
import zipfile

import pytest


# ─── Sample data ─────────────────────────────────────────────────────────────

SAMPLE_QUOTATIONS = [
    {
        "filename": "imported_1.pdf",
        "supplier": "Supplier A",
        "quotation_date": "2025-06-01",
        "currency": "USD",
        "document_type": "QUOTATION",
        "items": [{"model": "Item A", "unit_price": 100.0, "quantity": 2}],
    },
    {
        "filename": "imported_2.pdf",
        "supplier": "Supplier B",
        "quotation_date": "2025-06-15",
        "currency": "EUR",
        "document_type": "INVOICE",
        "items": [{"model": "Item B", "unit_price": 50.0, "quantity": 5}],
    },
]


def _make_import_json(quotations):
    """Build a JSON string in the format expected by /import/upload."""
    return json.dumps({"quotations": quotations, "count": len(quotations)}, indent=2)


def _make_import_zip(quotations, sha_override=None):
    """Build a ZIP file with quotations.json and optional SHA256.

    If sha_override is set, use that value for quotations.json.sha256
    instead of the correct hash (for mismatch testing).
    """
    payload = _make_import_json(quotations)
    actual_sha = hashlib.sha256(payload.encode()).hexdigest()
    sha_value = sha_override if sha_override else actual_sha

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("quotations.json", payload)
        if sha_value is not None:
            zf.writestr("quotations.json.sha256", sha_value)
    buf.seek(0)
    return buf, actual_sha


# ─── Local fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def master_with_quotations(seed_quotations):
    """Authenticated as 'master01' on a DB with 3 seeded quotations."""
    resp = seed_quotations.post("/auth/login", json={
        "username": "master01",
        "password": "masterpass",
        "remember_me": False,
    })
    assert resp.status_code == 200
    return seed_quotations


# ─── Authentication ──────────────────────────────────────────────────────────

class TestExportImportAuth:
    """Both export and import require admin or master role."""

    def test_export_requires_auth(self, app_client):
        """Unauthenticated GET /export returns 401."""
        resp = app_client.get("/export")
        assert resp.status_code == 401

    def test_export_requires_admin(self, user_client):
        """User role on GET /export returns 403."""
        resp = user_client.get("/export")
        assert resp.status_code == 403

    def test_import_requires_auth(self, app_client):
        """Unauthenticated POST /import/upload returns 401."""
        resp = app_client.post(
            "/import/upload",
            files={"file": ("test.zip", b"", "application/zip")},
        )
        assert resp.status_code == 401

    def test_import_requires_admin(self, user_client):
        """User role on POST /import/upload returns 403."""
        resp = user_client.post(
            "/import/upload",
            files={"file": ("test.zip", b"", "application/zip")},
        )
        assert resp.status_code == 403


# ─── Export ──────────────────────────────────────────────────────────────────

class TestExport:
    """GET /export — download all quotations as a ZIP with SHA256 integrity."""

    def test_export_empty(self, admin_client):
        """Export with no quotations returns a valid ZIP with count=0."""
        resp = admin_client.get("/export")
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "application/zip"
        cd = resp.headers.get("content-disposition", "")
        assert cd.startswith('attachment; filename="quodb_backup_')
        assert cd.endswith('.zip"')

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            assert "quotations.json" in names
            assert "quotations.json.sha256" in names

            data = json.loads(zf.read("quotations.json"))
            assert data["count"] == 0
            assert data["quotations"] == []

            stored_sha = zf.read("quotations.json.sha256").decode().strip()
            computed_sha = hashlib.sha256(
                zf.read("quotations.json")
            ).hexdigest()
            assert stored_sha == computed_sha

    def test_export_with_data(self, master_with_quotations):
        """Export with 3 seeded quotations returns a valid ZIP with count=3."""
        resp = master_with_quotations.get("/export")
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            data = json.loads(zf.read("quotations.json"))
            assert data["count"] == 3
            assert len(data["quotations"]) == 3
            # Items should be parsed as lists (not raw JSON strings)
            for q in data["quotations"]:
                assert isinstance(q["items"], list)

            stored_sha = zf.read("quotations.json.sha256").decode().strip()
            computed_sha = hashlib.sha256(
                zf.read("quotations.json")
            ).hexdigest()
            assert stored_sha == computed_sha


# ─── Import ──────────────────────────────────────────────────────────────────

class TestImport:
    """POST /import/upload — import quotations from JSON or ZIP."""

    def test_import_json_valid(self, admin_client):
        """A valid .json file with 2 quotations imports both."""
        payload = _make_import_json(SAMPLE_QUOTATIONS)
        resp = admin_client.post(
            "/import/upload",
            files={"file": ("data.json", payload.encode(), "application/json")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "imported"
        assert body["count"] == 2
        assert body["pdfs_restored"] == 0

    def test_import_zip_valid(self, admin_client):
        """A valid .zip with correct SHA256 imports both quotations."""
        zip_buf, _ = _make_import_zip(SAMPLE_QUOTATIONS, sha_override=None)
        resp = admin_client.post(
            "/import/upload",
            files={"file": ("backup.zip", zip_buf, "application/zip")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "imported"
        assert body["count"] == 2
        assert body["pdfs_restored"] == 0
        # No warning when SHA is present and valid
        assert "warning" not in body

    def test_import_zip_no_sha(self, admin_client):
        """A .zip without a SHA256 checksum is accepted with a warning."""
        zip_buf, _ = _make_import_zip(SAMPLE_QUOTATIONS, sha_override=None)
        # Remove the sha256 entry from the ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "r") as zf_in:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf_out:
                for name in zf_in.namelist():
                    if name == "quotations.json.sha256":
                        continue
                    zf_out.writestr(name, zf_in.read(name))
        buf.seek(0)

        resp = admin_client.post(
            "/import/upload",
            files={"file": ("backup.zip", buf, "application/zip")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "imported"
        assert body["count"] == 2
        assert "warning" in body
        assert "checksum" in body["warning"].lower()

    def test_import_invalid_extension(self, admin_client):
        """A .txt file returns 400."""
        resp = admin_client.post(
            "/import/upload",
            files={"file": ("data.txt", b"{}", "text/plain")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "Use .zip or .json"

    def test_import_empty_quotations(self, admin_client):
        """A .json with an empty quotations list returns 400."""
        payload = _make_import_json([])
        resp = admin_client.post(
            "/import/upload",
            files={"file": ("empty.json", payload.encode(), "application/json")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "No quotations found"

    def test_import_sha_mismatch(self, admin_client):
        """A .zip with a tampered SHA256 checksum returns 400."""
        zip_buf, _ = _make_import_zip(
            SAMPLE_QUOTATIONS, sha_override="a" * 64
        )
        resp = admin_client.post(
            "/import/upload",
            files={"file": ("tampered.zip", zip_buf, "application/zip")},
        )
        assert resp.status_code == 400
        assert "integrity" in resp.json()["error"].lower()

    def test_import_orphan_cleanup_empty_quotations(self, admin_client):
        """A ZIP with PDFs but empty quotations cleans up restored PDFs."""
        from backend.main import ARCHIVE_DIR

        # Build a ZIP with an archive PDF but empty quotations list
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            payload = json.dumps({"quotations": [], "count": 0})
            zf.writestr("quotations.json", payload)
            sha = hashlib.sha256(payload.encode()).hexdigest()
            zf.writestr("quotations.json.sha256", sha)
            zf.writestr("archive/test_doc.pdf", b"fake pdf content")
        buf.seek(0)
        expected_pdf = ARCHIVE_DIR / "test_doc.pdf"

        resp = admin_client.post(
            "/import/upload",
            files={"file": ("empty.zip", buf, "application/zip")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "No quotations found"

        # Verify the orphan PDF was cleaned up
        assert not expected_pdf.exists(), \
            "Orphan PDF should be deleted when import fails with no quotations"

    def test_import_orphan_cleanup_all_skipped(self, admin_client):
        """A ZIP with PDFs but all items empty cleans up restored PDFs."""
        from backend.main import ARCHIVE_DIR

        # Build a ZIP with an archive PDF and quotations that all have no items
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            payload = json.dumps({
                "quotations": [
                    {"filename": "bad.pdf", "items": []},
                ],
                "count": 1,
            })
            zf.writestr("quotations.json", payload)
            sha = hashlib.sha256(payload.encode()).hexdigest()
            zf.writestr("quotations.json.sha256", sha)
            zf.writestr("archive/bad.pdf", b"fake pdf")
        buf.seek(0)
        expected_pdf = ARCHIVE_DIR / "bad.pdf"

        resp = admin_client.post(
            "/import/upload",
            files={"file": ("bad.zip", buf, "application/zip")},
        )
        assert resp.status_code == 400
        assert "all had no items" in resp.json()["error"]

        # Verify the orphan PDF was cleaned up
        assert not expected_pdf.exists(), \
            "Orphan PDF should be deleted when import fails with no valid items"
