"""
backend/export_import.py — Secure export/import for QuoteHub.

Provides:
    - Password management (set, change, forgot recovery)
    - AES-256-GCM encrypted package creation (export)
    - Package decryption, validation, and MERGE import
    - Streaming I/O for large packages (>5 GB)

All operations are master-only. Endpoints in routes/files.py delegate here.
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import struct
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────
#
# Paths are NOT defined here — they are imported lazily at function call time
# from their canonical sources (backend.auth.DATA_DIR, backend.main.ARCHIVE_DIR)
# so the test framework can patch them via the standard conftest.py mocks.

# Crypto
AES_KEY_SIZE = 32                     # AES-256
GCM_NONCE_SIZE = 16                    # 128-bit nonce
GCM_TAG_SIZE = 16                     # 128-bit auth tag
SALT_SIZE = 16                        # PBKDF2 salt
KEY_VERSION = 1                       # Current key derivation scheme
PBKDF2_ITERATIONS = 600_000           # OWASP 2023 recommendation
CHUNK_SIZE = 64 * 1024 * 1024         # 64 MB streaming chunks

# Package binary header: salt(16) + nonce(16) + keyVersion(1) + iterations(8)
HEADER_FORMAT = struct.Struct(f'<{SALT_SIZE}s{GCM_NONCE_SIZE}sBQ')
HEADER_SIZE = HEADER_FORMAT.size       # 41 bytes


# ─── Password validation ─────────────────────────────────────────────────────

_COMMON_PATTERNS = ['password', '1234', 'admin', 'export', 'quodb', 'quote', 'abc123', 'qwerty', 'letmein']


def _has_sequential_chars(password: str) -> bool:
    """Check for 4+ sequential ASCII characters (ascending or descending)."""
    lower = password.lower()
    for i in range(len(lower) - 3):
        chunk = lower[i:i+4]
        if all(ord(chunk[j+1]) - ord(chunk[j]) == 1 for j in range(3)):
            return True
        if all(ord(chunk[j]) - ord(chunk[j+1]) == 1 for j in range(3)):
            return True
    return False


def validate_export_password(password: str) -> list[str]:
    """Validate export password strength. Returns list of error messages (empty = valid)."""
    errors = []
    if len(password) < 12:
        errors.append("Password must be at least 12 characters long")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one digit")
    if not any(not c.isalnum() for c in password):
        errors.append("Password must contain at least one special character")
    lower = password.lower()
    for pattern in _COMMON_PATTERNS:
        if pattern in lower:
            errors.append("Password contains a common pattern and is too guessable")
            break
    if _has_sequential_chars(password):
        errors.append("Password must not contain sequential characters (e.g. '1234' or 'abcd')")
    return errors

# ─── Password management ─────────────────────────────────────────────────────
#
# NOTE: The export password belongs to the file, not the system.
# No hash is stored. Each master provides a password at export time
# that is used once to encrypt the .quodb package. The same password
# must be provided at import time to decrypt it.
# No "forgot password" is possible — the password is unrecoverable.
#
# Password validation is still performed client-side and server-side
# in the endpoint, but no bcrypt hash is ever written to disk.


# ─── Crypto helpers ──────────────────────────────────────────────────────────

def _derive_key(password: str | bytes, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """Derive an AES-256 key from a password using PBKDF2-HMAC-SHA256.

    When *iterations* == 0, *password* is treated as a raw 32-byte key
    (used by auto-backup with internal key — no KDF needed).
    """
    if iterations == 0:
        return password if isinstance(password, bytes) else password.encode("utf-8")
    # Normalise to bytes
    pwd = password.encode("utf-8") if isinstance(password, str) else password
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=AES_KEY_SIZE, salt=salt, iterations=iterations)
    return kdf.derive(pwd)


def encrypt_file_at_rest(data: bytes, key: bytes) -> bytes:
    """Encrypt file content with AES-256-GCM using a raw 32-byte key.

    Format returned: nonce(16) + ciphertext + tag(16)
    (32 bytes of overhead regardless of file size.)

    Uses *key* directly with no PBKDF2 — this is for file-at-rest
    encryption where the key itself is a high-entropy random secret
    stored in an environment variable, so key stretching is unnecessary.

    Raises:
        ValueError: If *key* is not exactly 32 bytes.
    """
    if len(key) != AES_KEY_SIZE:
        raise ValueError(f"Key must be exactly {AES_KEY_SIZE} bytes (got {len(key)})")
    nonce = os.urandom(GCM_NONCE_SIZE)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(data) + encryptor.finalize()
    return nonce + ciphertext + encryptor.tag


def decrypt_file_at_rest(data: bytes, key: bytes) -> bytes:
    """Decrypt a file encrypted with :func:`encrypt_file_at_rest`.

    Args:
        data: nonce(16) + ciphertext + tag(16) from the earlier encryption.
        key: 32-byte AES key (must match the key used for encryption).

    Returns:
        Original plaintext bytes.

    Raises:
        cryptography.hazmat.primitives.ciphers ... InvalidTag:
            if the key is wrong or the data is corrupted.
        ValueError: If *key* is not exactly 32 bytes.
    """
    if len(key) != AES_KEY_SIZE:
        raise ValueError(f"Key must be exactly {AES_KEY_SIZE} bytes (got {len(key)})")
    nonce = data[:GCM_NONCE_SIZE]
    tag = data[-GCM_TAG_SIZE:]
    ciphertext = data[GCM_NONCE_SIZE:-GCM_TAG_SIZE]
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def get_encryption_key() -> bytes | None:
    """Read the file-at-rest encryption key from ``FILE_ENCRYPTION_KEY``.

    The env var must be a 64-character hex string (32 raw bytes).
    Returns ``None`` when the env var is unset or empty — in that case
    no encryption is applied and all files are stored as plaintext
    (backward-compatible behaviour).

    Raise ``ValueError`` if the env var is set but is not a valid
    64-char hex string.
    """
    hex_key = os.environ.get("FILE_ENCRYPTION_KEY", "")
    if not hex_key:
        return None
    key = bytes.fromhex(hex_key)
    if len(key) != AES_KEY_SIZE:
        raise ValueError(
            f"FILE_ENCRYPTION_KEY must decode to {AES_KEY_SIZE} bytes "
            f"(got {len(key)} from {len(hex_key)} hex chars)"
        )
    return key


def decrypt_file_to_temp(filepath: Path) -> Path:
    """Decrypt *filepath* to a temporary file and return its path.

    The caller **must** call ``unlink()`` on the returned path when
    the decrypted data is no longer needed.  (The temp file lives on
    disk — typically in ``/tmp/`` — to avoid filling memory with
    large decrypted payloads.)

    Raises:
        RuntimeError: If ``FILE_ENCRYPTION_KEY`` is not set.
    """
    import tempfile
    key = get_encryption_key()
    if key is None:
        raise RuntimeError("FILE_ENCRYPTION_KEY not set")
    encrypted = filepath.read_bytes()
    decrypted = decrypt_file_at_rest(encrypted, key)
    fd, tmp_path = tempfile.mkstemp(suffix=filepath.suffix)
    os.close(fd)
    with open(tmp_path, "wb") as f:
        f.write(decrypted)
    return Path(tmp_path)


def _file_sha256(path: Path) -> str:
    """Streaming SHA-256 of a file. Handles large files without loading into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def _copy_with_sha256(src: Path, dst: Path) -> str:
    """Copy a file with streaming SHA-256 hashing. Returns hex digest."""
    h = hashlib.sha256()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as s, open(dst, "wb") as d:
        while chunk := s.read(CHUNK_SIZE):
            h.update(chunk)
            d.write(chunk)
    return h.hexdigest()


def encrypt_package(
    zip_path: Path, output_path: Path, password: str | bytes,
    *,
    key_version: int = KEY_VERSION,
    iterations: int = PBKDF2_ITERATIONS,
) -> dict:
    """Encrypt a ZIP64 file with AES-256-GCM. Streaming, handles any file size.

    When *key_version* >= 2, *password* is treated as a raw 32-byte AES key
    (no PBKDF2 derivation — used by auto-backup).

    Output format:
        [41-byte header: salt(16) + nonce(16) + keyVersion(1) + iterations(8)]
        [ciphertext (variable)]
        [16-byte GCM authentication tag]

    Returns encryption metadata dict.
    """
    salt = os.urandom(SALT_SIZE) if iterations > 0 else b'\x00' * SALT_SIZE
    nonce = os.urandom(GCM_NONCE_SIZE)
    key = _derive_key(password, salt, iterations)

    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce))
    encryptor = cipher.encryptor()

    zip_size = os.path.getsize(zip_path)
    with open(output_path, "wb") as out:
        out.write(HEADER_FORMAT.pack(salt, nonce, key_version, iterations))
        with open(zip_path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                out.write(encryptor.update(chunk))
        encryptor.finalize()
        out.write(encryptor.tag)  # GCM auth tag (16 bytes)

    return {
        "algorithm": "AES-256-GCM",
        "kdf": "PBKDF2-HMAC-SHA256" if iterations > 0 else "none",
        "kdfIterations": iterations,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "keyVersion": key_version,
    }


def decrypt_package(package_path: Path, output_path: Path, password: str | bytes) -> dict:
    """Decrypt a .quodb file. Streaming, handles any file size.

    Reads the 41-byte header, then streams the ciphertext through AES-256-GCM.
    The 16-byte auth tag is read from the end of the file and verified during finalize().

    When the header's keyVersion >= 2, *password* is treated as a raw 32-byte
    AES key (no PBKDF2 — used by auto-backup).  The caller does not need to
    know the key version in advance; the header is self-describing.

    Returns encryption metadata from the header (salt, nonce, key_version, iterations).

    Raises:
        HTTPException 401 if auth tag verification fails (wrong password or corruption).
    """
    file_size = os.path.getsize(package_path)
    ciphertext_size = file_size - HEADER_SIZE - GCM_TAG_SIZE
    if ciphertext_size <= 0:
        raise HTTPException(status_code=400, detail="Invalid or corrupted package file")

    with open(package_path, "rb") as f:
        header_data = f.read(HEADER_SIZE)
        salt, nonce, key_version, iterations = HEADER_FORMAT.unpack(header_data)

        # Read GCM auth tag from end of file
        f.seek(-GCM_TAG_SIZE, os.SEEK_END)
        tag = f.read(GCM_TAG_SIZE)

        # Derive key (skip PBKDF2 for auto-backup, keyVersion >= 2)
        key = _derive_key(password, salt, iterations)

        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag))
        decryptor = cipher.decryptor()

        f.seek(HEADER_SIZE)
        with open(output_path, "wb") as out:
            remaining = ciphertext_size
            while remaining > 0:
                chunk_size = min(CHUNK_SIZE, remaining)
                chunk = f.read(chunk_size)
                remaining -= len(chunk)
                out.write(decryptor.update(chunk))
            try:
                decryptor.finalize()  # verifies GCM auth tag
            except Exception:
                raise HTTPException(
                    status_code=401,
                    detail="Wrong export password or corrupted package (authentication failed)"
                )

    return {
        "algorithm": "AES-256-GCM",
        "kdf": "PBKDF2-HMAC-SHA256",
        "kdfIterations": iterations,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "keyVersion": key_version,
    }


# ─── Record hash (deterministic dedup key) ───────────────────────────────────

def record_hash(record: dict) -> str:
    """Compute a deterministic SHA-256 hash from stable business fields.

    The hash covers supplier, quotation_date, document_type, and a
    canonical JSON serialization of items (sorted keys, no whitespace).
    Two records with identical business data produce the same hash.
    """
    items = record.get("items") or record.get("items") or []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (json.JSONDecodeError, TypeError):
            items = []
    if not isinstance(items, list):
        items = []

    canonical_items = json.dumps(items, sort_keys=True, separators=(",", ":"))
    hash_input = "|".join([
        str(record.get("supplier", "") or ""),
        str(record.get("quotation_date", "") or ""),
        str(record.get("document_type", "") or ""),
        canonical_items,
    ])
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()


# ─── Registry helpers ────────────────────────────────────────────────────────

from .db import get_db, get_machine_id, DB_PATH


def _create_registry_entry(export_id: str, system_id: str) -> int:
    """Create a STARTED registry entry. Returns the sequence number."""
    with get_db() as db:
        row = db.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 FROM export_registry WHERE system_id = ?",
            (system_id,)
        ).fetchone()
        seq = row[0]
        db.execute(
            "INSERT INTO export_registry (export_id, system_id, sequence_number, status) VALUES (?, ?, ?, 'STARTED')",
            (export_id, system_id, seq)
        )
    return seq


def _fail_export(export_id: str, error_detail: str):
    """Mark an export attempt as FAILED."""
    with get_db() as db:
        db.execute(
            "UPDATE export_registry SET status='FAILED', error_detail=?, completed_at=datetime('now') WHERE export_id=?",
            (error_detail, export_id)
        )


def _succeed_export(export_id: str, package_path: str, record_count: int, file_count: int, package_size: int):
    """Mark an export attempt as SUCCESS."""
    with get_db() as db:
        db.execute(
            """UPDATE export_registry SET status='SUCCESS', package_path=?, record_count=?,
               file_count=?, package_size_bytes=?, completed_at=datetime('now') WHERE export_id=?""",
            (package_path, record_count, file_count, package_size, export_id)
        )


def _get_latest_success_sequence(system_id: str) -> int | None:
    """Get the highest sequenceNumber among SUCCESS records for this system_id."""
    with get_db(readonly=True) as db:
        row = db.execute(
            "SELECT MAX(sequence_number) FROM export_registry WHERE system_id=? AND status='SUCCESS'",
            (system_id,)
        ).fetchone()
        return row[0] if row and row[0] is not None else None


# ─── Export workflow ─────────────────────────────────────────────────────────

def run_export(
    password: str | bytes,
    user: dict,
    *,
    output_path: Path | None = None,
    event_tag: str | None = None,
    key_version: int = KEY_VERSION,
) -> dict:
    """Execute the full export workflow.

    1. PRAGMA integrity_check
    2. Read all records, verify files exist
    3. Snapshot DB via backup() API
    4. Copy files with streaming SHA-256
    5. Build manifest + validation report
    6. Build ZIP64 archive
    7. AES-256-GCM encrypt
    8. Silent decrypt round-trip verify (only for manual exports, key_version == 1)

    *password* is a user-supplied string (manual) or a raw 32-byte key (auto-backup).
    When *key_version* >= 2 the password is used directly (no PBKDF2 — see
    :func:`encrypt_package`).

    *output_path* — if provided, the .quodb is moved here instead of returning a
    temp path for download streaming.
    *event_tag* — if provided, added to the manifest as ``exportType``
    (e.g. ``"daily"``, ``"pre-update"``).

    Returns report dict with exportId, packagePath, and summary.
    """
    from .main import APP_VERSION, ARCHIVE_DIR

    # ── 1. Generate identifiers ──
    export_id = str(uuid.uuid4())
    system_id = get_machine_id()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seq = _create_registry_entry(export_id, system_id)

    # ── 3. Integrity check ──
    with get_db(readonly=True) as db:
        rows = db.execute("PRAGMA integrity_check").fetchall()
        errors = [str(r[0]) for r in rows if r[0] != "ok"]
        if errors:
            _fail_export(export_id, "integrity_check failed: " + "; ".join(errors))
            raise HTTPException(status_code=500, detail="Database integrity check failed")

    # ── 4. Read records ──
    with get_db(readonly=True) as db:
        records = [dict(r) for r in db.execute("SELECT * FROM quotations ORDER BY id").fetchall()]

    # ── 5. Verify files ──
    missing_files = []
    file_entries = []
    for r in records:
        fn = r.get("filename", "")
        if not fn:
            continue
        fpath = ARCHIVE_DIR / fn
        if not fpath.exists():
            missing_files.append(fn)
        else:
            file_entries.append({"filename": fn, "path": fpath})

    if missing_files:
        _fail_export(export_id, "Missing required files: " + ", ".join(missing_files))
        raise HTTPException(status_code=500, detail="Missing required files: " + ", ".join(missing_files))

    # ── 6. Orphan check (WARNING only) ──
    archived_files = set(f.name for f in ARCHIVE_DIR.iterdir() if f.is_file())
    referenced_files = set(r["filename"] for r in records if r.get("filename"))
    orphan_files = sorted(archived_files - referenced_files)

    # ── 7. Build workspace ──
    workspace = Path(tempfile.mkdtemp(prefix=f"quodb_export_{export_id}_"))
    try:
        # ── 7a. Snapshot DB ──
        db_backup = workspace / "db" / "app-backup.db"
        db_backup.parent.mkdir(parents=True)
        with get_db(readonly=True) as src:
            dst_conn = sqlite3.connect(str(db_backup))
            with dst_conn:
                src.backup(dst_conn)
            dst_conn.close()
        db_checksum = _file_sha256(db_backup)

        # ── 7b. Copy and hash files ──
        files_meta = []
        for fe in file_entries:
            rel = "archive/" + fe["filename"]
            dst = workspace / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            sha = _copy_with_sha256(fe["path"], dst)
            files_meta.append({
                "path": "files/" + rel,
                "size": dst.stat().st_size,
                "sha256": sha,
                "targetPath": rel,
            })

        # ── 7c. Checksums file (internal diagnostics) ──
        checksums_dir = workspace / "checksums"
        checksums_dir.mkdir(parents=True)
        sha_lines = [f"{db_checksum}  db/app-backup.db"]
        for fm in files_meta:
            sha_lines.append(f"{fm['sha256']}  {fm['path']}")
        (checksums_dir / "sha256sums.txt").write_text("\n".join(sha_lines) + "\n")

        # ── 7d. Encryption metadata (for reference — binary header is source of truth) ──
        is_auto = key_version >= 2
        encryption_meta = {
            "algorithm": "AES-256-GCM",
            "kdf": "none" if is_auto else "PBKDF2-HMAC-SHA256",
            "kdfIterations": 0 if is_auto else PBKDF2_ITERATIONS,
            "keyVersion": key_version,
        }

        # ── 7e. Manifest ──
        manifest = {
            "formatVersion": "1",
            "exportId": export_id,
            "systemId": system_id,
            "sequenceNumber": seq,
            "exportedAtUtc": timestamp,
            "appVersion": APP_VERSION,
            "schemaVersion": 0,
            "exportType": event_tag or "manual",
            "masterUserId": user.get("id"),
            "masterDisplayName": user.get("username", "unknown"),
            "masterRole": user.get("role", "unknown"),
            "exportStatus": "SUCCESS",
            "dbChecksum": db_checksum,
            "encryption": encryption_meta,
            "files": files_meta,
            "recordCount": len(records),
            "fileCount": len(files_meta),
        }
        (workspace / "manifest.json").write_text(json.dumps(manifest, indent=2))

        # ── 7f. Validation report ──
        report = {
            "operation": "export",
            "exportId": export_id,
            "status": "SUCCESS",
            "severity": "INFO",
            "systemId": system_id,
            "summary": {
                "total_records": len(records),
                "total_files": len(files_meta),
                "errors": [],
                "warnings": [f"Orphan file on disk: {o}" for o in orphan_files],
            },
            "detail": {
                "integrity_check": {"passed": True, "errors": []},
                "missing_files": [],
                "orphan_files": orphan_files,
            },
            "timestamps": {"started_at": timestamp, "completed_at": timestamp},
        }
        (workspace / "validation-report.json").write_text(json.dumps(report, indent=2))

        # ── 8. Build ZIP64 archive ──
        zip_path = Path(tempfile.mkstemp(suffix=".zip")[1])
        try:
            with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for f in sorted(workspace.rglob("*")):
                    if f.is_file():
                        arcname = str(f.relative_to(workspace))
                        zf.write(str(f), arcname)

            # ── 9. Encrypt ──
            package_path = Path(tempfile.mkstemp(suffix=".quodb")[1])

            pkg_iterations = 0 if is_auto else PBKDF2_ITERATIONS
            encrypt_package(zip_path, package_path, password, key_version=key_version, iterations=pkg_iterations)
            package_size = package_path.stat().st_size

            # ── 10. Silent decrypt round-trip (skip for auto-backup — key is correct by construction) ──
            if not is_auto:
                _verify_path = Path(tempfile.mkstemp(suffix=".verify")[1])
                try:
                    decrypt_package(package_path, _verify_path, password)
                except HTTPException:
                    package_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=500,
                        detail="Password could not be verified for this export. Please choose a different password."
                    )
                finally:
                    _verify_path.unlink(missing_ok=True)

            # ── 11. Move to final location or keep temp ──
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(package_path), str(output_path))
                package_path = output_path
                package_size = package_path.stat().st_size

            # ── 12. Registry SUCCESS ──
            _succeed_export(export_id, str(package_path), len(records), len(files_meta), package_size)

            logger.info("Export completed", extra={
                'category': 'ADMIN' if not is_auto else 'SYS',
                'export_id': export_id,
                'event_tag': event_tag or "manual",
                'records': len(records),
                'files': len(files_meta),
                'package_bytes': package_size,
            })

            return {
                "exportId": export_id,
                "sequenceNumber": seq,
                "status": "SUCCESS",
                "recordCount": len(records),
                "fileCount": len(files_meta),
                "packageSizeBytes": package_size,
                "packagePath": str(package_path),
                "eventTag": event_tag,
                "warnings": [f"Orphan file on disk: {o}" for o in orphan_files] if orphan_files else [],
            }

        finally:
            zip_path.unlink(missing_ok=True)

    except HTTPException:
        _fail_export(export_id, "Export failed")
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    except Exception as e:
        _fail_export(export_id, str(e))
        shutil.rmtree(workspace, ignore_errors=True)
        logger.exception("Export failed", extra={'category': 'ADMIN', 'export_id': export_id})
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


# ─── Import workflow ─────────────────────────────────────────────────────────

def run_import(package: Path, password: str, *, dry_run: bool = False, force_system_id: bool = False) -> dict:
    """Execute the import workflow.

    Phase 1: Verify incoming package (decrypt, check checksums, systemId, sequence)
    Phase 2: Self-check live system (integrity_check, file refs)
    Phase 3: Compare incoming vs live (record hash, file SHA-256)
    Phase 4: Apply or dry-run report

    Args:
        package: Path to the .quodb file.
        password: Export password for decryption.
        dry_run: If True, only analyze and report — no changes applied.
        force_system_id: If True, proceed even if systemId doesn't match.

    Returns report dict with all counts and warnings.
    """
    from .main import ARCHIVE_DIR

    quarantine = Path(tempfile.mkdtemp(prefix="quodb_import_"))
    try:
        # ── Phase 1: Verify incoming package ──

        # 1a. Decrypt
        zip_path = quarantine / "package.zip"
        decrypt_package(package, zip_path, password)

        # 1b. Extract ZIP
        extract_dir = quarantine / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(str(extract_dir))

        # 1c. Read manifest
        manifest_path = extract_dir / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=400, detail="Missing manifest.json in package")
        with open(manifest_path) as f:
            manifest = json.load(f)

        import_id = str(uuid.uuid4())
        system_id = get_machine_id()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1d. Verify checksums
        checksum_errors = []
        for fm in manifest.get("files", []):
            fpath = extract_dir / fm["path"]
            if not fpath.exists():
                checksum_errors.append(f"Missing file in package: {fm['path']}")
                continue
            actual = _file_sha256(fpath)
            if actual != fm["sha256"]:
                checksum_errors.append(f"Checksum mismatch: {fm['path']}")
        # Check DB snapshot checksum
        db_backup_path = extract_dir / "db" / "app-backup.db"
        if db_backup_path.exists():
            actual_db = _file_sha256(db_backup_path)
            if actual_db != manifest.get("dbChecksum", ""):
                checksum_errors.append("Database snapshot checksum mismatch")

        if checksum_errors:
            return {
                "operation": "import",
                "importId": import_id,
                "status": "FAILED",
                "severity": "FATAL",
                "detail": "Package integrity check failed: " + "; ".join(checksum_errors),
            }

        # 1e. Check systemId
        manifest_system_id = manifest.get("systemId", "")
        system_id_match = manifest_system_id == system_id
        if not system_id_match and not force_system_id:
            return {
                "operation": "import",
                "importId": import_id,
                "status": "FAILED",
                "severity": "FATAL",
                "detail": f"System ID mismatch. Package was exported from '{manifest_system_id}', "
                         f"but this machine is '{system_id}'. Use force_system_id=true to override.",
            }

        # 1f. Check sequence number
        latest_seq = _get_latest_success_sequence(manifest_system_id)
        incoming_seq = manifest.get("sequenceNumber", 0)
        seq_warning = None
        if latest_seq is not None and incoming_seq < latest_seq:
            seq_warning = (
                f"This export (sequence {incoming_seq}) is older than the latest successful export "
                f"(sequence {latest_seq}) for system '{manifest_system_id}'."
            )

        # ── Phase 2: Self-check live system ──

        # 2a. integrity_check
        live_issues = []
        with get_db(readonly=True) as db:
            rows = db.execute("PRAGMA integrity_check").fetchall()
            integrity_errors = [str(r[0]) for r in rows if r[0] != "ok"]
            if integrity_errors:
                live_issues.append("Database integrity check failed: " + "; ".join(integrity_errors))

        # 2b. Check live file refs
        with get_db(readonly=True) as db:
            live_records = [dict(r) for r in db.execute("SELECT * FROM quotations ORDER BY id").fetchall()]
        live_file_refs = set()
        for r in live_records:
            fn = r.get("filename", "")
            if fn:
                live_file_refs.add(fn)
                if not (ARCHIVE_DIR / fn).exists():
                    live_issues.append(f"Live DB references file but it's missing from archive: {fn}")

        if live_issues:
            if dry_run:
                # Report but don't block dry-run
                pass
            else:
                return {
                    "operation": "import",
                    "importId": import_id,
                    "status": "FAILED",
                    "severity": "FATAL",
                    "detail": "Current system has issues: " + "; ".join(live_issues),
                }

        # ── Phase 3: Compare ──

        # 3a. Compute hashes for live records
        live_hashes = {record_hash(r): r for r in live_records}

        # 3b. Compute hashes for incoming records (from manifest — we need to read from DB snapshot)
        incoming_records = []
        if db_backup_path.exists():
            backup_conn = sqlite3.connect(str(db_backup_path))
            backup_conn.row_factory = sqlite3.Row
            try:
                incoming_records = [dict(r) for r in backup_conn.execute("SELECT * FROM quotations ORDER BY id").fetchall()]
            finally:
                backup_conn.close()

        # 3c. Compare records
        records_imported = 0
        records_skipped = 0
        staged_records = []
        for r in incoming_records:
            rh = record_hash(r)
            if rh in live_hashes:
                records_skipped += 1
            else:
                records_imported += 1
                staged_records.append(r)

        # 3d. Compare files
        files_imported = 0
        files_skipped = 0
        file_conflicts = []
        incoming_files_map = {}
        for fm in manifest.get("files", []):
            fpath = extract_dir / fm["path"]
            if fpath.exists():
                incoming_files_map[fm["targetPath"]] = {"path": fpath, "sha256": fm["sha256"], "size": fm["size"]}

        staged_files = []
        for target_path, finfo in incoming_files_map.items():
            target_full = ARCHIVE_DIR / Path(target_path).name  # just the filename part
            if not target_full.exists():
                files_imported += 1
                staged_files.append((finfo["path"], target_full))
            else:
                existing_sha = _file_sha256(target_full)
                if existing_sha == finfo["sha256"]:
                    files_skipped += 1
                else:
                    file_conflicts.append({
                        "path": target_path,
                        "existingSha256": existing_sha,
                        "incomingSha256": finfo["sha256"],
                    })

        # ── Build report ──
        incoming_count = len(incoming_records)
        report = {
            "operation": "import",
            "importId": import_id,
            "status": "SUCCESS" if not dry_run else "PREFLIGHT",
            "severity": "INFO",
            "systemId": system_id,
            "manifestSystemId": manifest_system_id,
            "systemIdMatch": system_id_match,
            "sequenceNumber": incoming_seq,
            "latestSequence": latest_seq,
            "summary": {
                "total_incoming_records": incoming_count,
                "total_incoming_files": len(incoming_files_map),
                "records_imported": records_imported,
                "records_skipped_duplicate": records_skipped,
                "files_imported": files_imported,
                "files_skipped_duplicate": files_skipped,
                "file_conflicts": len(file_conflicts),
                "errors": [],
                "warnings": [],
            },
            "detail": {
                "integrity_check": {"passed": not integrity_errors, "errors": integrity_errors},
                "checksum_errors": checksum_errors,
                "file_conflicts": file_conflicts,
                "live_issues": live_issues,
            },
            "timestamps": {"started_at": timestamp, "completed_at": timestamp},
            "exportAttribution": {
                "masterUserId": manifest.get("masterUserId"),
                "masterDisplayName": manifest.get("masterDisplayName"),
                "masterRole": manifest.get("masterRole"),
                "exportedAtUtc": manifest.get("exportedAtUtc"),
            },
        }

        if seq_warning:
            report["summary"]["warnings"].append(seq_warning)
            report["detail"]["older_export_warning"] = seq_warning
        if not system_id_match:
            report["summary"]["warnings"].append("System ID mismatch — import forced by user")
        if orphan_files := manifest.get("orphan_files", []):
            report["summary"]["warnings"].append(f"Source export had orphan files: {orphan_files}")

        # ── Phase 4: Apply (if not dry_run) ──
        if not dry_run and records_imported > 0:
            try:
                # Copy files to archive first (if file copy fails, no DB changes attempted)
                for src, dst in staged_files:
                    shutil.copy2(str(src), str(dst))

                # Insert records in transaction
                with get_db() as db:
                    for r in staged_records:
                        items_str = r.get("items")
                        if isinstance(items_str, (dict, list)):
                            items_str = json.dumps(items_str)
                        db.execute(
                            """INSERT INTO quotations (filename, supplier, quotation_date, currency, items, document_type, extraction_method)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (
                                r.get("filename", ""),
                                r.get("supplier", ""),
                                r.get("quotation_date", ""),
                                r.get("currency", ""),
                                items_str or "[]",
                                r.get("document_type", "unknown"),
                                r.get("extraction_method", "local"),
                            )
                        )
                    # Auto-committed on context exit

                logger.info("Import applied", extra={
                    'category': 'ADMIN',
                    'importId': import_id,
                    'records': records_imported,
                    'files': files_imported,
                })

                report["status"] = "SUCCESS"

            except Exception as e:
                # Rollback is automatic (get_db() rolls back on exception)
                # Clean up any files we copied before the error
                for _, dst in staged_files:
                    try:
                        dst.unlink(missing_ok=True)
                    except OSError:
                        pass
                logger.exception("Import apply failed", extra={'category': 'ADMIN', 'importId': import_id})
                report["status"] = "FAILED"
                report["severity"] = "FATAL"
                report["summary"]["errors"].append(str(e))

        elif not dry_run and records_imported == 0:
            report["summary"]["warnings"].append("No new records to import — all are duplicates")

        return report

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Import failed", extra={'category': 'ADMIN'})
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
    finally:
        shutil.rmtree(quarantine, ignore_errors=True)
