"""tests/test_encryption_at_rest.py — File-at-rest encryption tests.

Tests the encrypt_file_at_rest / decrypt_file_at_rest helpers and
the transparent encryption in the upload / read pipeline.
"""

import os
import secrets
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.export_import import (
    encrypt_file_at_rest,
    decrypt_file_at_rest,
    get_encryption_key,
    decrypt_file_to_temp,
    AES_KEY_SIZE,
)

# ─── A valid 32-byte (64 hex char) key for tests ────────────
TEST_HEX_KEY = secrets.token_hex(32)
TEST_KEY = bytes.fromhex(TEST_HEX_KEY)


class TestEncryptDecryptUnit:
    """Pure-function tests for encrypt_file_at_rest / decrypt_file_at_rest."""

    def test_round_trip(self):
        """Encrypt then decrypt returns the original data."""
        original = b"Hello, world! This is test data. " * 100
        encrypted = encrypt_file_at_rest(original, TEST_KEY)
        assert len(encrypted) == len(original) + 32  # nonce(16) + tag(16)
        assert encrypted != original  # not plaintext
        decrypted = decrypt_file_at_rest(encrypted, TEST_KEY)
        assert decrypted == original

    def test_wrong_key_fails(self):
        """Decrypting with the wrong key raises InvalidTag."""
        original = b"secret data"
        encrypted = encrypt_file_at_rest(original, TEST_KEY)
        wrong_key = secrets.token_bytes(32)
        with pytest.raises(Exception):
            decrypt_file_at_rest(encrypted, wrong_key)

    def test_different_output_each_time(self):
        """Same plaintext encrypted twice produces different ciphertext."""
        data = b"same data"
        e1 = encrypt_file_at_rest(data, TEST_KEY)
        e2 = encrypt_file_at_rest(data, TEST_KEY)
        assert e1 != e2  # different nonce → different ciphertext
        assert decrypt_file_at_rest(e1, TEST_KEY) == data
        assert decrypt_file_at_rest(e2, TEST_KEY) == data

    def test_empty_data(self):
        """Empty plaintext encrypts and decrypts correctly."""
        encrypted = encrypt_file_at_rest(b"", TEST_KEY)
        assert len(encrypted) == 32  # nonce + empty ciphertext + tag
        assert decrypt_file_at_rest(encrypted, TEST_KEY) == b""

    def test_wrong_key_length_raises(self):
        """Key must be exactly 32 bytes."""
        with pytest.raises(ValueError, match="must be exactly 32 bytes"):
            encrypt_file_at_rest(b"data", b"too-short")


class TestGetEncryptionKey:
    """Tests for get_encryption_key()."""

    def test_not_set_returns_none(self, monkeypatch):
        """When env var is not set, returns None."""
        monkeypatch.delenv("FILE_ENCRYPTION_KEY", raising=False)
        assert get_encryption_key() is None

    def test_valid_hex_key(self, monkeypatch):
        """A valid 64-char hex string returns a 32-byte key."""
        monkeypatch.setenv("FILE_ENCRYPTION_KEY", TEST_HEX_KEY)
        key = get_encryption_key()
        assert key == TEST_KEY
        assert len(key) == AES_KEY_SIZE

    def test_empty_string_returns_none(self, monkeypatch):
        """Empty string is treated as unset."""
        monkeypatch.setenv("FILE_ENCRYPTION_KEY", "")
        assert get_encryption_key() is None

    def test_invalid_hex_raises(self, monkeypatch):
        """Non-hex string raises ValueError."""
        monkeypatch.setenv("FILE_ENCRYPTION_KEY", "not-hex-string!")
        with pytest.raises(ValueError):
            get_encryption_key()


class TestDecryptFileToTemp:
    """Tests for decrypt_file_to_temp()."""

    def test_decrypts_to_temp_file(self, tmp_path, monkeypatch):
        """Decrypt a file on disk to a temp file and verify content."""
        monkeypatch.setenv("FILE_ENCRYPTION_KEY", TEST_HEX_KEY)

        original = b"PDF content would go here"
        encrypted_path = tmp_path / "test.pdf"
        encrypted_path.write_bytes(encrypt_file_at_rest(original, TEST_KEY))

        temp_path = decrypt_file_to_temp(encrypted_path)
        try:
            assert temp_path.exists()
            assert temp_path.read_bytes() == original
            assert temp_path.suffix == ".pdf"
        finally:
            temp_path.unlink(missing_ok=True)

    def test_without_key_raises(self, tmp_path, monkeypatch):
        """If FILE_ENCRYPTION_KEY is not set, raises RuntimeError."""
        monkeypatch.delenv("FILE_ENCRYPTION_KEY", raising=False)
        dummy = tmp_path / "dummy.pdf"
        dummy.write_bytes(b"some data")
        with pytest.raises(RuntimeError, match="FILE_ENCRYPTION_KEY not set"):
            decrypt_file_to_temp(dummy)


# ─── Upload pipeline test helpers ──────────────────────────

def _mock_get_current_user(request):
    return {"id": 1, "username": "test", "role": "master"}


# ─── Upload pipeline encryption tests ──────────────────────

class TestUploadWithEncryption:
    """Upload pipeline: file encryption at rest and transparent decryption."""

    @pytest.fixture(autouse=True)
    def _set_encryption_key(self, monkeypatch):
        """Enable encryption for all tests in this class."""
        monkeypatch.setenv("FILE_ENCRYPTION_KEY", TEST_HEX_KEY)

    @pytest.fixture(autouse=True)
    def _mock_auth_and_key(self, monkeypatch):
        """Mock auth check so uploads succeed."""
        patch_auth = patch("backend.auth.get_current_user", _mock_get_current_user)
        with patch_auth:
            yield

    @pytest.fixture
    def app_client_with_key(self, tmp_config, tmp_path):
        """TestClient with encryption key set via env var."""
        from backend.main import app
        from backend.db import init_db
        from backend.utils import CONFIG_PATH
        from backend.auth import DATA_DIR
        from backend.db import DB_PATH

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "temp").mkdir(exist_ok=True)
        (data_dir / "archive").mkdir(exist_ok=True)
        (data_dir / "images").mkdir(exist_ok=True)
        (data_dir / "secret.key").write_text("test-key")

        with patch("backend.utils.CONFIG_PATH", tmp_config), \
             patch("backend.db.DB_PATH", data_dir / "quotations.db"), \
             patch("backend.auth.DATA_DIR", data_dir), \
             patch("backend.main.DATA_DIR", data_dir), \
             patch("backend.main.ARCHIVE_DIR", data_dir / "archive"), \
             patch("backend.main.IMAGES_DIR", data_dir / "images"), \
             patch("backend.main.UPLOAD_DIR", data_dir / "temp"), \
             patch("backend.main.SECRET_KEY_PATH", data_dir / "secret.key"):

            from fastapi.testclient import TestClient
            init_db()
            client = TestClient(app)
            yield client, data_dir

    def test_uploaded_file_is_encrypted_on_disk(self, app_client_with_key):
        """After upload, the file on disk should be ciphertext, not plaintext."""
        client, data_dir = app_client_with_key
        original_content = b"%PDF-1.4 fake content for testing"

        resp = client.post(
            "/upload",
            files=[("files", ("test.pdf", io.BytesIO(original_content), "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded"] == 1

        # Read the file from UPLOAD_DIR
        uploaded_file = data_dir / "temp" / "test.pdf"
        assert uploaded_file.exists()
        disk_content = uploaded_file.read_bytes()

        # Disk content should be encrypted (not equal to original, and longer by 32 bytes)
        assert disk_content != original_content
        assert len(disk_content) == len(original_content) + 32

        # Decrypt and verify it matches the original
        decrypted = decrypt_file_at_rest(disk_content, TEST_KEY)
        assert decrypted == original_content

    def test_upload_without_key_stores_plaintext(self, tmp_config, tmp_path, monkeypatch):
        """Without FILE_ENCRYPTION_KEY, files are stored as plaintext (backward compat)."""
        monkeypatch.delenv("FILE_ENCRYPTION_KEY", raising=False)

        from backend.main import app
        from backend.db import init_db
        from backend.utils import CONFIG_PATH
        from backend.auth import DATA_DIR
        from backend.db import DB_PATH
        from fastapi.testclient import TestClient

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "temp").mkdir(exist_ok=True)
        (data_dir / "archive").mkdir(exist_ok=True)
        (data_dir / "images").mkdir(exist_ok=True)
        (data_dir / "secret.key").write_text("test-key")

        with patch("backend.utils.CONFIG_PATH", tmp_config), \
             patch("backend.db.DB_PATH", data_dir / "quotations.db"), \
             patch("backend.auth.DATA_DIR", data_dir), \
             patch("backend.main.DATA_DIR", data_dir), \
             patch("backend.main.ARCHIVE_DIR", data_dir / "archive"), \
             patch("backend.main.IMAGES_DIR", data_dir / "images"), \
             patch("backend.main.UPLOAD_DIR", data_dir / "temp"), \
             patch("backend.main.SECRET_KEY_PATH", data_dir / "secret.key"), \
             patch("backend.auth.get_current_user", _mock_get_current_user):

            init_db()
            client = TestClient(app)
            original = b"%PDF-1.4 test plaintext"
            resp = client.post(
                "/upload",
                files=[("files", ("plain.pdf", io.BytesIO(original), "application/pdf"))],
            )
            assert resp.status_code == 200
            assert resp.json()["uploaded"] == 1

            disk = (data_dir / "temp" / "plain.pdf").read_bytes()
            assert disk == original  # plaintext, no encryption overhead

    def test_encrypted_upload_accepts_xlsx(self, app_client_with_key):
        """XLSX files are also encrypted at rest."""
        client, data_dir = app_client_with_key
        original = b"PK\x03\x04xlsx content"

        resp = client.post(
            "/upload",
            files=[("files", ("data.xlsx", io.BytesIO(original), "application/octet-stream"))],
        )
        assert resp.status_code == 200
        assert resp.json()["uploaded"] == 1

        disk = (data_dir / "temp" / "data.xlsx").read_bytes()
        assert disk != original
        assert len(disk) == len(original) + 32
        assert decrypt_file_at_rest(disk, TEST_KEY) == original
