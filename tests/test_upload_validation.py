"""tests/test_upload_validation.py — Tests for upload validation (Step 2)."""

import io
from unittest.mock import patch

import pytest


def _mock_get_current_user(request):
    """Mock get_current_user to return a fake master user."""
    return {"id": 1, "username": "test", "role": "master"}


@pytest.fixture(autouse=True)
def _mock_auth():
    """Mock get_current_user for all tests in this module."""
    with patch("backend.auth.get_current_user", _mock_get_current_user):
        yield


class TestUploadValidation:
    """Tests for the /upload endpoint validation."""

    def test_valid_pdf_accepted(self, app_client):
        """A valid PDF file should be accepted."""
        pdf_content = b"%PDF-1.4 fake content for testing"
        resp = app_client.post(
            "/upload",
            files=[("files", ("test.pdf", io.BytesIO(pdf_content), "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 1
        assert len(data["errors"]) == 0

    def test_empty_file_rejected(self, app_client):
        """A 0-byte file should be rejected with an error."""
        resp = app_client.post(
            "/upload",
            files=[("files", ("empty.pdf", io.BytesIO(b""), "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        assert "Empty file" in data["errors"][0]["error"]

    def test_wrong_extension_rejected(self, app_client):
        """A .txt file should be rejected."""
        resp = app_client.post(
            "/upload",
            files=[("files", ("readme.txt", io.BytesIO(b"hello"), "text/plain"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        assert "Unsupported" in data["errors"][0]["error"]

    def test_xlsx_accepted(self, app_client):
        """A .xlsx file should be accepted."""
        xlsx_content = b"PK\x03\x04fake xlsx content"
        resp = app_client.post(
            "/upload",
            files=[("files", ("data.xlsx", io.BytesIO(xlsx_content), "application/octet-stream"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 1
        assert len(data["errors"]) == 0

    def test_multiple_files_mixed(self, app_client):
        """Mix of valid and invalid files: only valid ones uploaded."""
        resp = app_client.post(
            "/upload",
            files=[
                ("files", ("good.pdf", io.BytesIO(b"%PDF-1.4 content"), "application/pdf")),
                ("files", ("empty.pdf", io.BytesIO(b""), "application/pdf")),
                ("files", ("bad.txt", io.BytesIO(b"text"), "text/plain")),
            ],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 1
        assert len(data["errors"]) == 2
