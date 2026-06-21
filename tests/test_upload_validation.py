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

    def test_oversized_file_rejected(self, app_client):
        """A file exceeding max_upload_size_mb should be rejected at the network boundary."""
        # Default is 5 MB = 5242880 bytes; send 6 MB of data
        big_content = b"X" * (6 * 1024 * 1024)
        resp = app_client.post(
            "/upload",
            files=[("files", ("big.pdf", io.BytesIO(big_content), "application/pdf"))],
        )
        # Content-Length check rejects before reading body — returns 413
        assert resp.status_code == 413
        data = resp.json()
        assert "too large" in data["detail"].lower()

    def test_path_traversal_dotdot_rejected(self, app_client):
        """Filename with '..' should be rejected."""
        resp = app_client.post(
            "/upload",
            files=[("files", ("../../etc/passwd.pdf", io.BytesIO(b"%PDF-1.4 content"), "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        assert "Unsafe filename" in data["errors"][0]["error"]

    def test_path_traversal_slash_rejected(self, app_client):
        """Filename with '/' should be rejected."""
        resp = app_client.post(
            "/upload",
            files=[("files", ("subdir/malicious.pdf", io.BytesIO(b"%PDF-1.4 content"), "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        assert "Unsafe filename" in data["errors"][0]["error"]

    def test_path_traversal_backslash_rejected(self, app_client):
        """Filename with backslash should be rejected."""
        resp = app_client.post(
            "/upload",
            files=[("files", ("subdir\\malicious.pdf", io.BytesIO(b"%PDF-1.4 content"), "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        assert "Unsafe filename" in data["errors"][0]["error"]

    def test_filename_only_extension_rejected(self, app_client):
        """Filename without a name part (e.g. '.pdf') should be rejected."""
        resp = app_client.post(
            "/upload",
            files=[("files", (".pdf", io.BytesIO(b"%PDF-1.4 content"), "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        # pathlib treats '.pdf' as a hidden file with no extension, so it hits the extension check
        assert "Unsupported" in data["errors"][0]["error"]

    def test_path_traversal_in_mixed_batch(self, app_client):
        """Safe file accepted, traversal file rejected in same batch."""
        resp = app_client.post(
            "/upload",
            files=[
                ("files", ("good.pdf", io.BytesIO(b"%PDF-1.4 content"), "application/pdf")),
                ("files", ("../evil.xlsx", io.BytesIO(b"PK\x03\x04content"), "application/octet-stream")),
            ],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 1
        assert len(data["errors"]) == 1
        assert "Unsafe filename" in data["errors"][0]["error"]

    def test_pdf_wrong_magic_bytes_rejected(self, app_client):
        """.pdf with non-PDF content should be rejected."""
        resp = app_client.post(
            "/upload",
            files=[("files", ("fake.pdf", io.BytesIO(b"not a real pdf content"), "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        assert "Invalid file content" in data["errors"][0]["error"]

    def test_xlsx_wrong_magic_bytes_rejected(self, app_client):
        """.xlsx with non-XLSX content should be rejected."""
        resp = app_client.post(
            "/upload",
            files=[("files", ("fake.xlsx", io.BytesIO(b"not a real xlsx content"), "application/octet-stream"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        assert "Invalid file content" in data["errors"][0]["error"]

    def test_renamed_pdf_rejected(self, app_client):
        """.xlsx containing PDF magic bytes should be rejected."""
        resp = app_client.post(
            "/upload",
            files=[("files", ("renamed.xlsx", io.BytesIO(b"%PDF-1.4 fake xlsx"), "application/octet-stream"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 0
        assert len(data["errors"]) == 1
        assert "Invalid file content" in data["errors"][0]["error"]

    def test_magic_bytes_in_mixed_batch(self, app_client):
        """Safe file accepted, wrong magic bytes rejected in same batch."""
        resp = app_client.post(
            "/upload",
            files=[
                ("files", ("good.pdf", io.BytesIO(b"%PDF-1.4 content"), "application/pdf")),
                ("files", ("fake.pdf", io.BytesIO(b"not a pdf"), "application/pdf")),
            ],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 1
        assert len(data["errors"]) == 1
        assert "Invalid file content" in data["errors"][0]["error"]

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
