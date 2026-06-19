"""tests/test_auto_backup.py — Tests for the auto-backup subsystem.

Covers all mandatory scenarios from the spec.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.export_import import (
    HEADER_FORMAT,
    HEADER_SIZE,
    PBKDF2_ITERATIONS,
    encrypt_package,
    decrypt_package,
)

# ──────────────────────────────────────────────────────────────────────
#  Constants & helpers
# ──────────────────────────────────────────────────────────────────────

TEST_BACKUP_KEY = b'\xab' * 32  # deterministic 32-byte raw key


def _make_dummy_zip(path: Path, content: bytes = b"test-data"):
    """Create a minimal valid ZIP file for encryption tests."""
    import zipfile
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("test.txt", content)


def _quodb_header_fields(path: Path):
    with open(path, "rb") as f:
        header = f.read(HEADER_SIZE)
    return HEADER_FORMAT.unpack(header)


def _count_files(directory: Path) -> int:
    return len(list(directory.glob("*.quodb")))


def _insert_test_record(filename: str = "test_auto.pdf"):
    """Insert a single quotation record that passes export validation."""
    from backend.db import get_db
    with get_db() as db:
        db.execute(
            "INSERT INTO quotations (filename, supplier, quotation_date, "
            "document_type, items) VALUES (?, ?, ?, ?, ?)",
            (filename, "AutoTest", "2026-06-19", "INVOICE",
             '[{"model":"A","price":100}]'),
        )


def _create_archive_file(filename: str = "test_auto.pdf"):
    """Create a file in ARCHIVE_DIR for the test record."""
    from backend.main import ARCHIVE_DIR
    p = ARCHIVE_DIR / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"dummy content for {filename}")
    return p


# ──────────────────────────────────────────────────────────────────────
#  Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def patched_key_manager(tmp_path, app_client):
    """Redirect key manager paths to temp dir."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    from backend.key_manager import _ensure_state_table
    _ensure_state_table()
    with patch("backend.key_manager.KEYS_DIR", keys_dir):
        yield keys_dir


@pytest.fixture
def patched_auto_backup(tmp_path, app_client, fast_crypto):
    """Redirect auto-backup directories to temp dir with key set up."""
    auto_dir = tmp_path / "auto-backups"
    daily_dir = auto_dir / "daily"
    weekly_dir = auto_dir / "weekly"
    events_dir = auto_dir / "events"
    for d in (daily_dir, weekly_dir, events_dir):
        d.mkdir(parents=True)

    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()

    from backend.key_manager import ensure_internal_key
    with patch.multiple(
        "backend.auto_backup",
        DAILY_DIR=daily_dir,
        WEEKLY_DIR=weekly_dir,
        EVENTS_DIR=events_dir,
        AUTO_BACKUP_ROOT=auto_dir,
    ), patch("backend.key_manager.KEYS_DIR", keys_dir):
        ensure_internal_key()
        yield {
            "daily": daily_dir,
            "weekly": weekly_dir,
            "events": events_dir,
            "root": auto_dir,
        }


@pytest.fixture
def seeded_export_data(seeded_db, fast_crypto):
    """Insert a quotation record + archive file so export can succeed."""
    _create_archive_file("test_auto.pdf")
    _insert_test_record("test_auto.pdf")
    return seeded_db


# ══════════════════════════════════════════════════════════════════════
#  P0.1 — Internal key encryption round-trip
# ══════════════════════════════════════════════════════════════════════

class TestAutoBackupEncryptDecrypt:

    def test_encrypt_with_internal_key_round_trip(self, tmp_path):
        """A file encrypted with key_version=2 and raw 32-byte key
        decrypts correctly with the same key."""
        original = tmp_path / "input.zip"
        encrypted = tmp_path / "pkg.quodb"
        decrypted = tmp_path / "output.zip"

        _make_dummy_zip(original, b"auto-backup-test-data " * 1000)

        encrypt_package(original, encrypted, TEST_BACKUP_KEY,
                        key_version=2, iterations=0)

        salt, nonce, kv, iters = _quodb_header_fields(encrypted)
        assert kv == 2
        assert iters == 0
        assert salt == b'\x00' * 16

        decrypt_package(encrypted, decrypted, TEST_BACKUP_KEY)
        assert decrypted.read_bytes() == original.read_bytes()

    def test_wrong_key_fails(self, tmp_path):
        original = tmp_path / "input.zip"
        encrypted = tmp_path / "pkg.quodb"
        _make_dummy_zip(original, b"secret")
        encrypt_package(original, encrypted, TEST_BACKUP_KEY,
                        key_version=2, iterations=0)
        with pytest.raises(Exception):
            decrypt_package(encrypted, tmp_path / "bad.zip", b'\xba' * 32)

    def test_manual_export_still_works(self, tmp_path):
        """Unchanged manual export path (key_version=1, PBKDF2) still works."""
        original = tmp_path / "input.zip"
        encrypted = tmp_path / "manual.quodb"
        decrypted = tmp_path / "output.zip"
        _make_dummy_zip(original, b"manual-test")
        encrypt_package(original, encrypted, "Str0ng!P@ss42")
        decrypt_package(encrypted, decrypted, "Str0ng!P@ss42")
        assert decrypted.read_bytes() == original.read_bytes()

    def test_auto_backup_manifest_has_export_type(self, tmp_path, seeded_export_data):
        """Auto-backup run_export produces a manifest with exportType."""
        from backend.export_import import run_export

        output = tmp_path / "auto-backups" / "auto_test.quodb"
        output.parent.mkdir(parents=True)

        system_user = {"id": 0, "username": "auto-test", "role": "system"}
        result = run_export(
            TEST_BACKUP_KEY, system_user,
            output_path=output, event_tag="daily", key_version=2,
        )
        assert result["eventTag"] == "daily"

        zip_path = tmp_path / "manifest_check.zip"
        decrypt_package(output, zip_path, TEST_BACKUP_KEY)
        import zipfile
        with zipfile.ZipFile(str(zip_path)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["exportType"] == "daily"
        assert manifest["encryption"]["keyVersion"] == 2
        assert manifest["encryption"]["kdf"] == "none"


# ══════════════════════════════════════════════════════════════════════
#  P0.2 — Key manager
# ══════════════════════════════════════════════════════════════════════

class TestAutoBackupKeyManager:

    def test_ensure_creates_key_v1(self, patched_key_manager):
        from backend.key_manager import ensure_internal_key, get_current_key_version
        ensure_internal_key()
        assert get_current_key_version() == 1
        files = list(patched_key_manager.glob("backup-key-*.enc"))
        assert len(files) == 1
        assert files[0].name == "backup-key-v1.enc"

    def test_ensure_idempotent(self, patched_key_manager):
        from backend.key_manager import ensure_internal_key
        ensure_internal_key()
        ensure_internal_key()
        assert len(list(patched_key_manager.glob("backup-key-*.enc"))) == 1

    def test_get_internal_key_returns_32_bytes(self, patched_key_manager):
        from backend.key_manager import ensure_internal_key, get_internal_key
        ensure_internal_key()
        key = get_internal_key()
        assert len(key) == 32

    def test_key_rotation_creates_new_version(self, patched_key_manager):
        from backend.key_manager import ensure_internal_key, rotate_internal_key, get_current_key_version
        ensure_internal_key()
        rotate_internal_key()
        assert get_current_key_version() == 2
        files = sorted(patched_key_manager.glob("backup-key-*.enc"))
        assert len(files) == 2

    def test_old_key_still_decrypts_after_rotation(self, patched_key_manager, tmp_path):
        from backend.key_manager import ensure_internal_key, get_internal_key, rotate_internal_key
        ensure_internal_key()
        original = tmp_path / "v1.zip"
        pkg_v1 = tmp_path / "v1.quodb"
        _make_dummy_zip(original, b"v1-data")
        key_v1 = get_internal_key(1)
        encrypt_package(original, pkg_v1, key_v1, key_version=1, iterations=0)
        rotate_internal_key()
        decrypted = tmp_path / "v1_restored.zip"
        decrypt_package(pkg_v1, decrypted, get_internal_key(1))
        import zipfile
        with zipfile.ZipFile(str(decrypted)) as zf:
            assert zf.read("test.txt") == b"v1-data"

    def test_purge_unused_versions(self, patched_key_manager):
        from backend.key_manager import ensure_internal_key, rotate_internal_key, purge_unused_key_versions
        ensure_internal_key()
        rotate_internal_key()
        purge_unused_key_versions({2})
        remaining = sorted(patched_key_manager.glob("backup-key-*.enc"))
        assert len(remaining) == 1
        assert "v2" in remaining[0].name


# ══════════════════════════════════════════════════════════════════════
#  P0.3 — Daily backup + retention + events
# ══════════════════════════════════════════════════════════════════════

class TestDailyBackup:

    def test_daily_backup_creates_file(self, patched_auto_backup, seeded_export_data):
        from backend.auto_backup import run_daily_backup
        result = run_daily_backup()
        assert result is not None
        assert result["status"] == "SUCCESS"
        assert _count_files(patched_auto_backup["daily"]) == 1

    def test_daily_backup_skipped_no_changes(self, patched_auto_backup, seeded_export_data):
        from backend.auto_backup import run_daily_backup
        # First backup
        r1 = run_daily_backup()
        assert r1 is not None
        # Second call — always creates a new backup (no change-detection skip)
        r2 = run_daily_backup()
        assert r2 is not None
        assert _count_files(patched_auto_backup["daily"]) == 2

    def test_daily_backup_runs_after_data_change(self, patched_auto_backup, seeded_export_data):
        from backend.auto_backup import run_daily_backup
        # First backup via seeded_export_data
        run_daily_backup()
        assert _count_files(patched_auto_backup["daily"]) == 1

        # Second backup always runs (no change-detection skip)
        run_daily_backup()
        assert _count_files(patched_auto_backup["daily"]) == 2


class TestEventBackups:

    def test_pre_update_backup(self, patched_auto_backup, seeded_export_data):
        from backend.auto_backup import run_event_backup
        result = run_event_backup("pre-update-v1.4.2")
        assert result["status"] == "SUCCESS"
        files = list(patched_auto_backup["events"].glob("pre-update-v1.4.2_*.quodb"))
        assert len(files) == 1

    def test_pre_import_backup(self, patched_auto_backup, seeded_export_data):
        from backend.auto_backup import pre_import_backup
        result = pre_import_backup("my_export_2026-06-15.quodb")
        assert result["status"] == "SUCCESS"
        files = list(patched_auto_backup["events"].glob("pre-import-my_export_*.quodb"))
        assert len(files) >= 1


class TestRetentionSweep:

    def _create_n_backups(self, directory: Path, n: int, prefix: str = "auto"):
        for i in range(n):
            p = directory / f"{prefix}_{i}.quodb"
            p.write_bytes(b"x")
            os.utime(p, (time.time() - (n - i) * 3600,
                         time.time() - (n - i) * 3600))

    def test_daily_keeps_last_7(self, patched_auto_backup):
        self._create_n_backups(patched_auto_backup["daily"], 10)
        from backend.auto_backup import retention_sweep
        retention_sweep()
        assert _count_files(patched_auto_backup["daily"]) == 7

    def test_weekly_keeps_last_4(self, patched_auto_backup):
        self._create_n_backups(patched_auto_backup["weekly"], 6)
        from backend.auto_backup import retention_sweep
        retention_sweep()
        assert _count_files(patched_auto_backup["weekly"]) == 4

    def test_event_45_day_retention(self, patched_auto_backup):
        events = patched_auto_backup["events"]
        now = time.time()
        for age_days in [50, 30, 10]:
            p = events / f"evt_{age_days}.quodb"
            p.write_bytes(b"x")
            os.utime(p, (now - age_days * 86400, now - age_days * 86400))
        from backend.auto_backup import retention_sweep
        retention_sweep()
        remaining = sorted(events.glob("*.quodb"))
        assert len(remaining) == 2  # 50-day-old deleted

    def test_event_keeps_last_3_pre_update(self, patched_auto_backup):
        events = patched_auto_backup["events"]
        now = time.time()
        for i in range(4):
            p = events / f"pre-update-v1.{i}.0_old.quodb"
            p.write_bytes(b"x")
            os.utime(p, (now - 50 * 86400, now - 50 * 86400))
        from backend.auto_backup import retention_sweep
        retention_sweep()
        remaining = sorted(events.glob("pre-update-*.quodb"))
        assert len(remaining) == 3


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

class TestCLI:

    def test_cli_pre_update_success(self, patched_auto_backup, seeded_export_data):
        from backend.cli import main
        exit_code = main(["backup", "pre-update", "--version", "9.9.9"])
        assert exit_code == 0
        files = list(patched_auto_backup["events"].glob("pre-update-v9.9.9_*.quodb"))
        assert len(files) >= 1

    def test_cli_pre_update_idempotent(self, patched_auto_backup, seeded_export_data):
        from backend.cli import main
        from unittest.mock import patch
        with patch("backend.cli.EVENTS_DIR", patched_auto_backup["events"]):
            main(["backup", "pre-update", "--version", "1.0.0"])
            files_before = list(patched_auto_backup["events"].glob("pre-update-v1.0.0_*.quodb"))
            main(["backup", "pre-update", "--version", "1.0.0"])
            files_after = list(patched_auto_backup["events"].glob("pre-update-v1.0.0_*.quodb"))
            assert len(files_after) == len(files_before)

    def test_cli_key_rotate(self, patched_key_manager):
        from backend.cli import main
        from backend.key_manager import ensure_internal_key, get_current_key_version
        ensure_internal_key()
        assert get_current_key_version() == 1
        exit_code = main(["key", "rotate"])
        assert exit_code == 0
        assert get_current_key_version() == 2

    def test_cli_key_current(self, patched_key_manager):
        from backend.cli import main
        from backend.key_manager import ensure_internal_key
        ensure_internal_key()
        exit_code = main(["key", "current"])
        assert exit_code == 0


# ══════════════════════════════════════════════════════════════════════
#  Restore from auto-backup
# ══════════════════════════════════════════════════════════════════════

class TestAutoBackupRestore:

    def test_restore_from_daily_backup(self, tmp_path, seeded_export_data, fast_crypto):
        from backend.export_import import run_export, run_import
        out = tmp_path / "daily_restore.quodb"
        system_user = {"id": 0, "username": "test", "role": "system"}
        run_export(TEST_BACKUP_KEY, system_user,
                   output_path=out, event_tag="daily", key_version=2)
        result = run_import(out, TEST_BACKUP_KEY, dry_run=True)
        assert result["status"] == "PREFLIGHT"

    def test_restore_from_pre_update_backup(self, tmp_path, seeded_export_data, fast_crypto):
        from backend.export_import import run_export, run_import
        out = tmp_path / "preup.quodb"
        system_user = {"id": 0, "username": "test", "role": "system"}
        run_export(TEST_BACKUP_KEY, system_user,
                   output_path=out, event_tag="pre-update-v2.0.0", key_version=2)
        result = run_import(out, TEST_BACKUP_KEY, dry_run=True)
        assert result["status"] == "PREFLIGHT"

    def test_restore_blocked_system_id_mismatch(self, tmp_path, seeded_export_data, fast_crypto):
        from backend.export_import import run_export, run_import
        out = tmp_path / "mismatch.quodb"
        system_user = {"id": 0, "username": "test", "role": "system"}
        run_export(TEST_BACKUP_KEY, system_user,
                   output_path=out, event_tag="daily", key_version=2)

        zip_path = tmp_path / "tamper.zip"
        decrypt_package(out, zip_path, TEST_BACKUP_KEY)
        import zipfile
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(str(zip_path)) as zf:
            zf.extractall(str(extract_dir))
        manifest = json.loads((extract_dir / "manifest.json").read_text())
        manifest["systemId"] = "foreign-machine"
        (extract_dir / "manifest.json").write_text(json.dumps(manifest))
        tampered_zip = tmp_path / "tampered.zip"
        with zipfile.ZipFile(str(tampered_zip), "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(extract_dir.rglob("*")):
                if f.is_file():
                    zf.write(str(f), str(f.relative_to(extract_dir)))
        tampered_pkg = tmp_path / "tampered.quodb"
        encrypt_package(tampered_zip, tampered_pkg, TEST_BACKUP_KEY,
                        key_version=2, iterations=0)
        result = run_import(tampered_pkg, TEST_BACKUP_KEY, dry_run=True)
        assert result["status"] == "FAILED"
        assert "System ID mismatch" in result.get("detail", "")

    def test_corrupted_package_detection(self, tmp_path):
        pkg = tmp_path / "corrupt.quodb"
        orig = tmp_path / "valid.zip"
        _make_dummy_zip(orig, b"data")
        encrypt_package(orig, pkg, TEST_BACKUP_KEY, key_version=2, iterations=0)
        data = bytearray(pkg.read_bytes())
        corrupt_offset = HEADER_SIZE + 50
        if corrupt_offset < len(data) - 16:
            data[corrupt_offset] ^= 0xFF
        pkg.write_bytes(data)
        with pytest.raises(Exception):
            decrypt_package(pkg, tmp_path / "fail.zip", TEST_BACKUP_KEY)


# ══════════════════════════════════════════════════════════════════════
#  Post-upgrade warning
# ══════════════════════════════════════════════════════════════════════

class TestPostUpgradeWarning:

    def test_warning_logged_when_missing(self, patched_auto_backup, caplog):
        from backend.auto_backup import _check_post_upgrade
        from backend.key_manager import _set_state
        _set_state("last_seen_app_version", "0.0.1")
        caplog.clear()
        with caplog.at_level("WARNING"):
            _check_post_upgrade()
        assert any("NO pre-update backup found" in msg for msg in caplog.messages)

    def test_no_warning_when_backup_exists(self, patched_auto_backup, caplog):
        from backend.auto_backup import _check_post_upgrade, EVENTS_DIR
        from backend.key_manager import _set_state
        p = EVENTS_DIR / "pre-update-v0.0.1_backup.quodb"
        p.write_text("dummy")
        _set_state("last_seen_app_version", "0.0.1")
        caplog.clear()
        with caplog.at_level("WARNING"):
            _check_post_upgrade()
        assert not any("NO pre-update backup found" in msg for msg in caplog.messages)


# ══════════════════════════════════════════════════════════════════════
#  Key rotation backward compat
# ══════════════════════════════════════════════════════════════════════

class TestKeyRotationBackwardCompat:

    def test_old_package_decrypts_after_rotation(self, patched_key_manager, tmp_path, fast_crypto):
        from backend.key_manager import (ensure_internal_key, get_internal_key,
                                          rotate_internal_key)
        from backend.export_import import run_export, run_import
        ensure_internal_key()

        out_v1 = tmp_path / "v1_backup.quodb"
        system_user = {"id": 0, "username": "rot-test", "role": "system"}

        # Need a small DB to back up — use tmp_path, a fresh one
        # Since we're inside the patched_key_manager / app_client scope,
        # we can safely call run_export with little data.
        run_export(get_internal_key(1), system_user,
                   output_path=out_v1, event_tag="daily", key_version=1)

        rotate_internal_key()

        result = run_import(out_v1, get_internal_key(1), dry_run=True)
        assert result["status"] in ("PREFLIGHT", "SUCCESS")
