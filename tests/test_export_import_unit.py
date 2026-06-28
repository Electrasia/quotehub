"""tests/test_export_import_unit.py — Unit tests for backend/export_import.py.

Tests pure functions and functions with minimal external dependencies.
Heavier workflow tests (run_export, run_import) live in test_export_import_api.py.

Requires: cryptography, pytest
"""

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from backend.export_import import (
    validate_export_password,
    record_hash,
    _derive_key,
    encrypt_package,
    decrypt_package,
    _file_sha256,
    _copy_with_sha256,
    PBKDF2_ITERATIONS,
    AES_KEY_SIZE,
    SALT_SIZE,
)


# ═══════════════════════════════════════════════════════════════
# ─── validate_export_password ─────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestValidateExportPassword:
    """Password strength validation — pure function, no deps."""

    def test_valid_password(self):
        """A password meeting all strength rules returns no errors."""
        errors = validate_export_password("Str0ng!P@ss42")
        assert errors == []

    def test_too_short(self):
        """Password under 12 characters is rejected."""
        errors = validate_export_password("Ab1!def")
        assert any("12 characters" in e for e in errors)

    def test_missing_uppercase(self):
        """Password with no uppercase letter is rejected."""
        errors = validate_export_password("lowercase123!@")
        assert any("uppercase" in e.lower() for e in errors)

    def test_missing_lowercase(self):
        """Password with no lowercase letter is rejected."""
        errors = validate_export_password("UPPERCASE123!@")
        assert any("lowercase" in e.lower() for e in errors)

    def test_missing_digit(self):
        """Password with no digit is rejected."""
        errors = validate_export_password("NoDigits!@#")
        assert any("digit" in e.lower() for e in errors)

    def test_missing_special_char(self):
        """Password with no special character is rejected."""
        errors = validate_export_password("NoSpecialChar1a")
        assert any("special" in e.lower() for e in errors)

    def test_common_pattern(self):
        """Password matching a common pattern is rejected."""
        errors = validate_export_password("Password123!")
        assert any("common" in e.lower() for e in errors)

    def test_multiple_errors(self):
        """Very weak password accumulates multiple errors."""
        errors = validate_export_password("abc")
        assert len(errors) >= 3

    def test_empty_password(self):
        """Empty password is rejected."""
        errors = validate_export_password("")
        assert len(errors) >= 1

    def test_export_password_sequential_chars_rejected(self):
        """Export password with 4+ sequential characters is rejected."""
        errors = validate_export_password("Abcdefg1!2345")
        assert any("sequential" in e.lower() for e in errors)

    def test_export_password_username_check_skipped(self):
        """Export password validator has no username rule (no param)."""
        # 'admin' appears in password but export validator has no username check
        errors = validate_export_password("Admin1234!@Xx")
        # Should only get common pattern error, NOT username error
        assert not any("username" in e.lower() for e in errors)


# ═══════════════════════════════════════════════════════════════
# ─── record_hash ──────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestRecordHash:
    """Deterministic record hashing for dedup — pure function, no deps."""

    def test_same_data_same_hash(self):
        """Two identical records produce the same hash."""
        r1 = {"supplier": "Acme", "quotation_date": "2025-01-01",
              "document_type": "INVOICE", "items": [{"model": "A", "price": 10}]}
        r2 = dict(r1)  # same data, different object
        assert record_hash(r1) == record_hash(r2)

    def test_different_supplier_different_hash(self):
        """Records differing only in supplier produce different hashes."""
        r1 = {"supplier": "Acme", "quotation_date": "2025-01-01",
              "document_type": "INVOICE", "items": [{"model": "A", "price": 10}]}
        r2 = {**r1, "supplier": "Beta"}
        assert record_hash(r1) != record_hash(r2)

    def test_items_normalization_string_vs_list(self):
        """Items as JSON string vs parsed list produce the same hash."""
        r1 = {"supplier": "Acme", "quotation_date": "2025-01-01",
              "document_type": "INVOICE",
              "items": [{"model": "A", "price": 10}]}
        r2 = {**r1, "items": json.dumps(r1["items"])}
        assert record_hash(r1) == record_hash(r2)

    def test_missing_keys_fallback(self):
        """Missing optional keys fall back to empty strings."""
        r = {"supplier": "Acme"}  # no date, doc_type, items
        h = record_hash(r)
        assert isinstance(h, str) and len(h) == 64  # SHA-256 hex

    def test_empty_items_list(self):
        """Empty items list is handled gracefully."""
        r = {"supplier": "Acme", "quotation_date": "2025-01-01",
             "document_type": "INVOICE", "items": []}
        h = record_hash(r)
        assert isinstance(h, str) and len(h) == 64


# ═══════════════════════════════════════════════════════════════
# ─── Crypto helpers ───────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestDeriveKey:
    """PBKDF2 key derivation — uses real crypto, but fast with low iterations."""

    def test_deterministic(self):
        """Same password + salt → same key."""
        salt = b"0123456789abcdef"  # 16 bytes
        k1 = _derive_key("mypassword", salt, iterations=1)
        k2 = _derive_key("mypassword", salt, iterations=1)
        assert k1 == k2
        assert len(k1) == AES_KEY_SIZE  # 32 bytes

    def test_different_salt_different_key(self):
        """Same password, different salt → different key."""
        salt1 = b"0123456789abcdef"
        salt2 = b"fedcba9876543210"
        k1 = _derive_key("mypassword", salt1, iterations=1)
        k2 = _derive_key("mypassword", salt2, iterations=1)
        assert k1 != k2

    def test_different_password_different_key(self):
        """Different password, same salt → different key."""
        salt = b"0123456789abcdef"
        k1 = _derive_key("password_a", salt, iterations=1)
        k2 = _derive_key("password_b", salt, iterations=1)
        assert k1 != k2


class TestEncryptDecryptRoundTrip:
    """AES-256-GCM encrypt → decrypt round-trip with temp files."""

    PASSWORD = "Str0ng!P@ss42"

    def test_round_trip(self, tmp_path):
        """Encrypt a file then decrypt it — result matches original."""
        original = tmp_path / "original.zip"
        encrypted = tmp_path / "package.quodb"
        decrypted = tmp_path / "restored.zip"

        original.write_bytes(b"hello world this is test data " * 1000)

        # Encrypt
        encrypt_package(original, encrypted, self.PASSWORD)
        assert encrypted.exists()
        assert encrypted.stat().st_size > 0

        # Decrypt
        decrypt_package(encrypted, decrypted, self.PASSWORD)
        assert decrypted.exists()
        assert decrypted.read_bytes() == original.read_bytes()

    def test_wrong_password_fails(self, tmp_path):
        """Decrypting with wrong password raises an exception."""
        original = tmp_path / "original.zip"
        encrypted = tmp_path / "package.quodb"

        original.write_bytes(b"secret data")

        encrypt_package(original, encrypted, self.PASSWORD)

        with pytest.raises(Exception):
            decrypt_package(encrypted, tmp_path / "bad.zip", "WrongPassword1!")

    def test_different_passwords_produce_different_output(self, tmp_path):
        """Same plaintext encrypted with different passwords gives different ciphertext."""
        original = tmp_path / "original.zip"
        original.write_bytes(b"same data")

        e1 = tmp_path / "pkg1.quodb"
        e2 = tmp_path / "pkg2.quodb"

        encrypt_package(original, e1, self.PASSWORD)
        encrypt_package(original, e2, "OtherStr0ng!Pass")

        assert e1.read_bytes() != e2.read_bytes()

    def test_large_file_streaming(self, tmp_path):
        """Encrypt and decrypt a file larger than CHUNK_SIZE (64MB)."""
        chunk_size = 64 * 1024 * 1024  # 64 MB
        original = tmp_path / "large.zip"
        # Write 2 chunks of deterministic data
        data = b"abcdefghijklmnop" * (chunk_size // 16)  # exactly 64 MB
        with open(original, "wb") as f:
            f.write(data)
            f.write(data[:1024])  # 64 MB + 1 KB

        encrypted = tmp_path / "large.quodb"
        decrypted = tmp_path / "large_restored.zip"

        encrypt_package(original, encrypted, self.PASSWORD)
        decrypt_package(encrypted, decrypted, self.PASSWORD)

        assert decrypted.stat().st_size == original.stat().st_size
        assert decrypted.read_bytes() == original.read_bytes()


# ═══════════════════════════════════════════════════════════════
# ─── File helpers ─────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class TestFileHelpers:
    """_file_sha256 and _copy_with_sha256 — file I/O with temp files."""

    def test_file_sha256_deterministic(self, tmp_path):
        """Same file content produces the same SHA-256."""
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        h1 = _file_sha256(f)
        h2 = _file_sha256(f)
        assert h1 == h2
        assert len(h1) == 64

    def test_file_sha256_matches_hashlib(self, tmp_path):
        """_file_sha256 output matches hashlib.sha256().hexdigest()."""
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert _file_sha256(f) == expected

    def test_copy_with_sha256(self, tmp_path):
        """Copied file has identical content and SHA-256 is returned."""
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst" / "copied.bin"
        src.write_bytes(b"data to copy")
        sha = _copy_with_sha256(src, dst)
        assert dst.exists()
        assert dst.read_bytes() == src.read_bytes()
        assert sha == hashlib.sha256(b"data to copy").hexdigest()


# ═══════════════════════════════════════════════════════════════
# ─── Password management ──────────────────────────────────────
#
# NOTE: Password management functions (export_password_exists,
# set_export_password, etc.) were removed in v0.061.0.
# The export password is now per-file and never stored.
# Password validation is still tested above in TestValidateExportPassword.
