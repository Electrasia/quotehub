"""tests/test_sse.py — SSE process-stream endpoint tests.

Covers POST /process-stream: authentication gates and error paths
that occur before the async generator starts (file resolution failures).

Does NOT test: the successful SSE event flow (requires real files + AI),
the process_lock contention path (requires mocking), or parse/extraction
error events (inside the async generator).
"""

import uuid

import pytest


# ─── Local fixtures ───────────────────────────────────────────────────────────

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

class TestProcessStreamAuth:
    """POST /process-stream requires admin or master role."""

    def test_requires_auth(self, app_client):
        """Unauthenticated requests return 401."""
        resp = app_client.post("/process-stream", json={})
        assert resp.status_code == 401

    def test_requires_admin(self, user_client):
        """User role returns 403 (admin/master only)."""
        resp = user_client.post("/process-stream", json={})
        assert resp.status_code == 403


# ─── Error paths ─────────────────────────────────────────────────────────────

class TestProcessStreamErrors:
    """Error paths that abort before the SSE generator starts."""

    def test_file_not_found(self, admin_client):
        """A non-existent file_id returns 404 — File not found."""
        resp = admin_client.post(
            "/process-stream", json={"file_id": uuid.uuid4().hex}
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "File not found"

    def test_file_not_on_disk(self, admin_client, isolated_uploaded_files):
        """An entry in uploaded_files whose filepath doesn't exist -> 404."""
        isolated_uploaded_files.append({
            "file_id": "test-no-file",
            "filename": "test.pdf",
            "filepath": "/tmp/nonexistent/test.pdf",
            "status": "uploaded",
            "num_pages": 1,
            "pages": [],
            "uploaded_by": "test",
        })
        resp = admin_client.post(
            "/process-stream", json={"file_id": "test-no-file"}
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "File not found on disk"
