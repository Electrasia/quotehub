"""tests/test_files_crud.py — File CRUD endpoint tests.

Covers confirm, update, delete, skip, remove-file, next-file, and clear
endpoints — all the CRUD operations on the /files router.

What is tested:
  - Authentication gates (401/403) for every endpoint
  - Update (POST /update): update a seeded quotation, non-existent ID, invalid body
  - Delete (POST /delete): delete seeded quotations, empty IDs, non-existent IDs
  - Confirm (POST /confirm): error path when no file is uploaded (404)
  - Skip (POST /skip): error path when no file is uploaded (404)
  - Remove-file (POST /remove-file): non-existent file_id (404)
  - Remove-file (POST /remove-file): verifies disk cleanup (file + images deleted)
  - Clear (POST /clear): always succeeds regardless of upload state
  - Clear (POST /clear): verifies disk cleanup (all files + images deleted)
  - Next-file (GET /next-file): empty state — no files uploaded

What is NOT tested (requires file uploads, modifies global state):
  - Upload (POST /upload) — requires multipart form data
  - Confirm/skip/remove-file with real uploaded files (beyond cleanup)
  - Next-file with real uploaded files
  - Process-stream (POST /process-stream) — requires real file + async SSE
  - Export (GET /export) — requires quotations + real files
  - Import (POST /import/upload) — requires ZIP/JSON file

The global uploaded_files list (backend.main.uploaded_files) is shared across
all tests in the session. Tests that modify the list use ``isolated_uploaded_files``
to save/restore the global state around each test.
"""

import pytest
from pathlib import Path


# ─── Local fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def master_with_quotations(seed_quotations):
    """Authenticated as 'master01' on a DB with 3 seeded quotations."""
    resp = seed_quotations.post("/auth/login", json={
        "username": "master01",
        "password": "Mast3r!Pass12",
        "remember_me": False,
    })
    assert resp.status_code == 200
    return seed_quotations


@pytest.fixture
def isolated_uploaded_files():
    """Save/restore uploaded_files so the test can add entries safely.

    The global uploaded_files list persists across all tests in the
    session.  This fixture captures its current state, clears it for
    the test, and restores the original state when the test finishes.
    """
    from backend.main import uploaded_files
    saved = list(uploaded_files)
    uploaded_files.clear()
    yield uploaded_files
    uploaded_files.clear()
    uploaded_files.extend(saved)


# ─── Authentication ──────────────────────────────────────────────────────────

class TestFilesAuth:
    """All files CRUD endpoints require admin or master role."""

    def test_post_endpoints_require_auth(self, app_client):
        """Unauthenticated POST requests return 401 across all endpoints."""
        endpoints = [
            "/confirm", "/update", "/delete",
            "/skip", "/remove-file", "/clear",
        ]
        for ep in endpoints:
            resp = app_client.post(ep, json={})
            assert resp.status_code == 401, f"{ep} should return 401"

    def test_get_next_file_requires_auth(self, app_client):
        """Unauthenticated GET /next-file returns 401."""
        resp = app_client.get("/next-file")
        assert resp.status_code == 401

    def test_post_endpoints_require_admin(self, user_client):
        """User role returns 403 on all POST endpoints (admin/master only)."""
        endpoints = [
            "/confirm", "/update", "/delete",
            "/skip", "/remove-file", "/clear",
        ]
        for ep in endpoints:
            resp = user_client.post(ep, json={})
            assert resp.status_code == 403, f"{ep} should return 403 for user role"

    def test_get_next_file_requires_admin(self, user_client):
        """User role returns 403 on GET /next-file."""
        resp = user_client.get("/next-file")
        assert resp.status_code == 403


# ─── Update ──────────────────────────────────────────────────────────────────

class TestUpdate:
    """POST /update — update an existing quotation."""

    def test_update_quotation(self, master_with_quotations):
        """Updating a seeded quotation should return success."""
        resp = master_with_quotations.post("/update", json={
            "id": 1,
            "data": {"supplier": "Updated Supplier"},
        })
        assert resp.status_code == 200
        assert resp.json() == {"status": "updated"}

    def test_update_nonexistent(self, master_with_quotations):
        """Updating a non-existent quotation ID returns 404."""
        resp = master_with_quotations.post("/update", json={
            "id": 9999,
            "data": {"supplier": "test"},
        })
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Quotation not found"

    def test_update_invalid_body(self, master_client):
        """Missing required Pydantic fields return 422."""
        resp = master_client.post("/update", json={})
        assert resp.status_code == 422


# ─── Delete ──────────────────────────────────────────────────────────────────

class TestDelete:
    """POST /delete — delete quotations by ID."""

    def test_delete_quotations(self, master_with_quotations):
        """Deleting seeded quotations returns the expected count."""
        resp = master_with_quotations.post("/delete", json={"ids": [1, 2, 3]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["count"] == 3
        assert body["files_deleted"] == 0  # no PDFs exist in test env

    def test_delete_empty_ids(self, master_client):
        """Empty ID list returns early with 'nothing to delete'."""
        resp = master_client.post("/delete", json={"ids": []})
        assert resp.status_code == 200
        assert resp.json()["status"] == "nothing to delete"

    def test_delete_nonexistent(self, master_client):
        """Non-existing IDs return 'nothing to delete' with detail."""
        resp = master_client.post("/delete", json={"ids": [9999]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "nothing to delete"
        assert "No matching quotations found" in body["detail"]

    def test_delete_invalid_body(self, master_client):
        """Missing required field returns 422."""
        resp = master_client.post("/delete", json={})
        assert resp.status_code == 422


# ─── Confirm / Save ──────────────────────────────────────────────────────────

class TestConfirm:
    """POST /confirm — error-state tests (no file uploaded)."""

    def test_confirm_file_not_found(self, master_client):
        """Confirming a non-existent file_id returns 404."""
        resp = master_client.post("/confirm", json={
            "file_id": "nonexistent",
            "data": {"items": [{}]},
        })
        assert resp.status_code == 404
        assert resp.json()["detail"] == "File not found"


# ─── Skip ────────────────────────────────────────────────────────────────────

class TestSkip:
    """POST /skip — error-state tests (no file uploaded)."""

    def test_skip_file_not_found(self, master_client):
        """Skipping a non-existent file_id returns 404."""
        resp = master_client.post("/skip", json={"file_id": "nonexistent"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "File not found"


# ─── Remove File ─────────────────────────────────────────────────────────────

class TestRemoveFile:
    """POST /remove-file."""

    def test_remove_not_found(self, master_client):
        """Removing a non-existent file_id returns 404."""
        resp = master_client.post("/remove-file", json={"file_id": "nonexistent"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "File not found"


class TestRemoveFileCleanup:
    """POST /remove-file — verifies disk cleanup."""

    def test_remove_file_cleans_images(self, admin_client, isolated_uploaded_files):
        """Removing a file should also delete its generated page images."""
        from backend.main import UPLOAD_DIR, IMAGES_DIR

        # Create a fake uploaded file on disk
        filepath = UPLOAD_DIR / "test_cleanup.pdf"
        filepath.write_text("fake pdf content")

        # Create fake page images
        img_dir = IMAGES_DIR / "test_cleanup"
        img_dir.mkdir(parents=True)
        (img_dir / "page_1.png").write_text("fake png")
        (img_dir / "page_2.png").write_text("fake png")

        # Add to uploaded_files
        file_id = "cleanup-test-file"
        isolated_uploaded_files.append({
            "file_id": file_id,
            "filename": "test_cleanup.pdf",
            "filepath": str(filepath),
            "status": "processed",
            "num_pages": 2,
            "pages": ["/images/test_cleanup/page_1.png", "/images/test_cleanup/page_2.png"],
            "uploaded_by": "admin",
        })

        # Verify pre-conditions
        assert filepath.is_file()
        assert img_dir.is_dir()

        # Call remove-file
        resp = admin_client.post("/remove-file", json={"file_id": file_id})
        assert resp.status_code == 200
        assert resp.json() == {"status": "removed", "file_id": file_id}

        # Verify cleanup
        assert not filepath.exists(), "Source file should be deleted"
        assert not img_dir.exists(), "Image directory should be deleted"


# ─── Clear ───────────────────────────────────────────────────────────────────

class TestClear:
    """POST /clear — clear all uploaded files."""

    def test_clear_files(self, admin_client):
        """Clearing uploaded files should always return success."""
        resp = admin_client.post("/clear")
        assert resp.status_code == 200
        assert resp.json() == {"status": "cleared"}


class TestClearCleanup:
    """POST /clear — verifies disk cleanup."""

    def test_clear_cleans_files_and_images(self, admin_client, isolated_uploaded_files):
        """Clearing should delete all uploaded files and their page images."""
        from backend.main import UPLOAD_DIR, IMAGES_DIR

        # Create two fake uploaded files with images
        entries = []
        for name in ["doc_a", "doc_b"]:
            filepath = UPLOAD_DIR / f"{name}.pdf"
            filepath.write_text("fake pdf")
            img_dir = IMAGES_DIR / name
            img_dir.mkdir(parents=True)
            (img_dir / "page_1.png").write_text("fake png")
            entries.append({
                "file_id": f"clear-{name}",
                "filename": f"{name}.pdf",
                "filepath": str(filepath),
                "status": "uploaded",
                "num_pages": 1,
                "pages": [f"/images/{name}/page_1.png"],
                "uploaded_by": "admin",
            })

        # Add entries and verify pre-conditions
        for e in entries:
            isolated_uploaded_files.append(e)
            assert Path(e["filepath"]).is_file()
            assert (IMAGES_DIR / Path(e["filepath"]).stem).is_dir()

        # Call clear
        resp = admin_client.post("/clear")
        assert resp.status_code == 200
        assert resp.json() == {"status": "cleared"}

        # Verify cleanup: source files and images should be gone
        for e in entries:
            assert not Path(e["filepath"]).exists(), \
                f"Source file {e['filepath']} should be deleted"
            assert not (IMAGES_DIR / Path(e["filepath"]).stem).exists(), \
                f"Images for {e['filename']} should be deleted"


# ─── Next File ───────────────────────────────────────────────────────────────

class TestNextFile:
    """GET /next-file."""

    def test_next_file_empty(self, admin_client):
        """When no files are uploaded, returns file_id=None and file_index=-1."""
        resp = admin_client.get("/next-file")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_id"] is None
        assert body["file_index"] == -1
