"""
backend/key_manager.py — Internal Backup Key management for auto-backup.

Key hierarchy (2-layer, machine-bound):
    machine_id (data/machine_id)
        │
        ▼  HKDF-SHA256(salt="quodb-ink-v1")
    wrapping_key  (32 bytes, NEVER stored on disk)
        │
        ▼  AES-256-GCM encrypt
    backup-key-v{N}.enc   (one file per version, 0600 permissions)

Key rotation:
    - rotate_internal_key() creates backup-key-v{N+1}.enc.
    - Old key files are NOT deleted — still needed for old backup packages.
    - Retention sweep purges key versions no longer referenced by any package.

The current key version is stored in the _auto_backup_state table.
"""

import logging
import os
import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from .db import DATA_DIR, get_db, get_machine_id

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

KEYS_DIR = DATA_DIR / "keys"
AES_KEY_SIZE = 32  # AES-256
GCM_NONCE_SIZE = 12
GCM_TAG_SIZE = 16
HKDF_SALT = b"quodb-ink-v1"
HKDF_INFO = b"auto-backup-key-wrapping"


# ─── Internal helpers ────────────────────────────────────────────────────────


def _get_keys_dir() -> Path:
    """Return (and create if needed) the keys directory."""
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(KEYS_DIR, 0o700)
    except PermissionError:
        pass
    return KEYS_DIR


def _derive_wrapping_key() -> bytes:
    """Derive the 32-byte wrapping key from the machine ID via HKDF.

    This key is NEVER stored on disk.  It is re-derived on every access.
    If *machine_id* changes (data volume moved to a different machine),
    all backup keys become unrecoverable — which is the intended
    security property.
    """
    machine_id = get_machine_id()
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=HKDF_SALT,
        info=HKDF_INFO,
    )
    return hkdf.derive(machine_id.encode("utf-8"))


# ─── State table helpers (shared with auto_backup.py) ─────────────────────


def _ensure_state_table():
    """Create the _auto_backup_state table if it does not exist."""
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS _auto_backup_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)


def _get_state(key: str, default: str | None = None) -> str | None:
    """Read a value from _auto_backup_state."""
    _ensure_state_table()
    with get_db(readonly=True) as db:
        row = db.execute(
            "SELECT value FROM _auto_backup_state WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else default


def _set_state(key: str, value: str):
    """Write a value to _auto_backup_state."""
    _ensure_state_table()
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO _auto_backup_state (key, value) VALUES (?, ?)",
            (key, value),
        )


# ─── Public API ─────────────────────────────────────────────────────────────


def ensure_internal_key():
    """Generate the initial Internal Backup Key if none exists.

    Safe to call multiple times — only acts on first invocation.
    Logs at INFO level on first creation.
    """
    _ensure_state_table()
    _get_keys_dir()

    existing = sorted(KEYS_DIR.glob("backup-key-*.enc"))
    if existing:
        return  # already initialised

    backup_key = os.urandom(AES_KEY_SIZE)
    wrapping_key = _derive_wrapping_key()
    nonce = os.urandom(GCM_NONCE_SIZE)

    cipher = Cipher(algorithms.AES(wrapping_key), modes.GCM(nonce))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(backup_key) + encryptor.finalize()

    path = KEYS_DIR / "backup-key-v1.enc"
    # Layout: [12B nonce][16B GCM tag][32B encrypted key] = 60 bytes
    path.write_bytes(nonce + encryptor.tag + ciphertext)
    path.chmod(0o600)

    _set_state("current_key_version", "1")
    logger.info("Auto-backup key v1 generated at %s", path)


def get_internal_key(key_version: int | None = None) -> bytes:
    """Retrieve the Internal Backup Key for the given version.

    If *key_version* is None, the current (latest) version is used.
    Raises FileNotFoundError if the key file does not exist.
    Raises ValueError if the key cannot be decrypted (wrapping key mismatch,
    corruption, or machine ID change).
    """
    if key_version is None:
        v = _get_state("current_key_version", "1")
        key_version = int(v)

    path = KEYS_DIR / f"backup-key-v{key_version}.enc"
    if not path.exists():
        raise FileNotFoundError(
            f"Auto-backup key v{key_version} not found at {path}. "
            "Has the key been initialised (ensure_internal_key) or was it deleted?"
        )

    data = path.read_bytes()
    if len(data) != GCM_NONCE_SIZE + GCM_TAG_SIZE + AES_KEY_SIZE:
        raise ValueError(
            f"Corrupted key file {path}: expected 60 bytes, got {len(data)}"
        )

    nonce = data[:GCM_NONCE_SIZE]
    tag = data[GCM_NONCE_SIZE:GCM_NONCE_SIZE + GCM_TAG_SIZE]
    ciphertext = data[GCM_NONCE_SIZE + GCM_TAG_SIZE:]

    wrapping_key = _derive_wrapping_key()
    cipher = Cipher(algorithms.AES(wrapping_key), modes.GCM(nonce, tag))
    decryptor = cipher.decryptor()

    try:
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    except Exception as exc:
        raise ValueError(
            f"Failed to decrypt backup key v{key_version}. "
            "The wrapping key may have changed (machine ID modified or data "
            "volume moved to a different machine)."
        ) from exc

    return plaintext


def get_current_key_version() -> int:
    """Return the current (latest) key version number."""
    v = _get_state("current_key_version", "1")
    return int(v)


def rotate_internal_key() -> int:
    """Generate a new Internal Backup Key (version +1) and set it as current.

    Returns the new version number.
    The previous key file is preserved — old packages can still be decrypted.
    """
    current = get_current_key_version()
    new_version = current + 1
    new_key = os.urandom(AES_KEY_SIZE)
    wrapping_key = _derive_wrapping_key()
    nonce = os.urandom(GCM_NONCE_SIZE)

    cipher = Cipher(algorithms.AES(wrapping_key), modes.GCM(nonce))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(new_key) + encryptor.finalize()

    path = KEYS_DIR / f"backup-key-v{new_version}.enc"
    path.write_bytes(nonce + encryptor.tag + ciphertext)
    path.chmod(0o600)

    _set_state("current_key_version", str(new_version))
    logger.info("Auto-backup key rotated: v%d → v%d", current, new_version)
    return new_version


# ─── Key cleanup (used by retention sweep) ────────────────────────────


def purge_unused_key_versions(used_versions: set[int]):
    """Delete key files for versions NOT in *used_versions*.

    Called by the retention sweep after consolidating which auto-backup
    packages are still retained.  Versions referenced by those packages
    are kept; all others are removed.
    """
    for path in KEYS_DIR.glob("backup-key-*.enc"):
        v = int(path.stem.split("-v")[-1])
        if v not in used_versions:
            path.unlink()
            logger.info("Purged unused key v%d: %s", v, path)
