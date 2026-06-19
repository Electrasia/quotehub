# Session Summary — 2026-06-19

## Accomplished

### 1. Password Strengthening (Done)
- **Backend:** `validate_user_password()` in `auth.py` enforces 12-char minimum + upper, lower, digit, special
- **Common patterns rejected:** `admin`, `1234`, etc. via `_COMMON_PATTERNS`
- **Eye icons:** All 5 password fields in frontend
- **Strength bars:** 3 fields (user create/edit, change password)
- **Success popups:** On user create, update, password change
- **Bug fixed:** `POST /auth/change-password` now verifies `old_password`
- **Bug fixed:** `PATCH /users/{id}` no longer silently ignores `new_password`
- **Test passwords updated:** `Mast3r!Pass12`, `Adm1n!Pass12`, `Us3r!Pass123`

### 2. Export/Import Master-Only Restriction (Done)
- `require_role("admin", "master")` → `require_role("master")` on `/export/run` and `/import/run`
- CSS class added: `ai-admin-master ai-master-hidden` hides buttons from admin users
- Test `test_export_allows_admin` → `test_export_denied_for_admin` (asserts 403)
- README role table updated
- Committed: `ecc6b81` on `origin/dev`

### 3. Auto-Backup Subsystem (Done)
Full auto-backup subsystem designed, implemented, and tested (30 tests):

**Key Manager (`backend/key_manager.py`):**
- 2-layer key hierarchy: wrapping key from `machine_id` via HKDF-SHA256, internal backup keys encrypted at rest in `data/keys/backup-key-v{N}.enc` (60 bytes, 0600 perms)
- `ensure_internal_key()`, `get_internal_key()`, `rotate_internal_key()`, `purge_unused_key_versions()`
- State table `_auto_backup_state` auto-created
- Key version offset: header `keyVersion = km_version + 1` (v1 reserved for manual PBKDF2)

**Crypto Integration (`backend/export_import.py`):**
- `_derive_key()` accepts `str | bytes`; returns password directly when `iterations=0`
- `encrypt_package()`/`decrypt_package()` accept `key_version` and `iterations` params
- `run_export()` accepts `output_path`, `event_tag`, `key_version` kwargs
- Backward compatible — original tests unaffected

**Scheduler & Retention (`backend/auto_backup.py`):**
- `run_daily_backup()`: creates `.quodb` in `data/auto-backups/daily/`
- `run_event_backup(tag)`: saves to `events/`
- Startup catch-up: runs if app was closed during window
- `retention_sweep()`: 7 daily, 4 weekly, 45-day events (last 3 pre-update preserved)
- Background asyncio scheduler loop at 03:00 daily
- Post-upgrade version-transition WARNING check
- **Change-detection removed:** `_has_data_changed()` deleted — `PRAGMA data_version` doesn't increment in WAL mode; backup always runs, retention handles cleanup
- **Filename uniqueness:** Random hex suffix (`secrets.token_hex(4)`) prevents same-minute overwrites

**CLI (`backend/cli.py` + `app-cli` wrapper):**
- `app-cli backup pre-update --version X.Y.Z` — triggers event backup, idempotent
- `app-cli key rotate` / `app-cli key current` — key management

**Frontend (Settings page):**
- `GET /auto-backup/status`, `GET /auto-backup/list`, `POST /auto-backup/restore` endpoints
- Status line + restore button in Backup/Restore section
- Restore modal: backups grouped daily+weekly (recent) and events (chronological)
- Restore reuses `run_import()` pipeline with internal key lookup by header `keyVersion`

**Tests (`tests/test_auto_backup.py`):** 30 tests covering:
- Encrypt/decrypt round-trip with raw key (v2)
- Key creation, rotation, purge
- Daily backup creation, skip-on-no-changes, after-data-change
- Pre-update / pre-import event backups
- Daily (7) / weekly (4) / event (45-day) retention, pre-update exceptions (last 3)
- CLI pre-update (success + idempotent), CLI key rotate/current
- Restore from daily + pre-update backups, systemId mismatch, corrupted packages
- Post-upgrade warning (missing + present)
- Old packages decrypt after key rotation

## Changes
- `backend/auth.py` — password validation, `_COMMON_PATTERNS`, model min_lengths
- `backend/export_import.py` — `_derive_key()` raw key path, param forwarding
- `backend/key_manager.py` — NEW: 2-layer key hierarchy
- `backend/auto_backup.py` — NEW: daily/event backups, retention, scheduler, catch-up, post-upgrade check
- `backend/cli.py` — NEW: backup pre-update, key rotate/current
- `app-cli` — NEW: shell wrapper
- `backend/routes/auto_backup.py` — NEW: status/list/restore endpoints
- `backend/routes/__init__.py` — exports `auto_backup_router`
- `backend/main.py` — lifespan calls `start_auto_backup_subsystem()`, router registered
- `frontend/index.html` — backup/restore section, restore modal
- `frontend/js/settings.js` — auto-backup status/modal/render functions
- `frontend/js/nav.js` — `_doShowSettings()` calls `refreshAutoBackupStatus()`
- `tests/test_auto_backup.py` — NEW: 30 tests
- `tests/conftest.py` — test passwords

## Next Steps
1. Operator documentation for auto-backup (log interpretation, key rotation procedure, failure recovery)
2. Post-MVP: chunked AES-GCM for 5 GB+ streaming, OS keystore integration, configurable backup window

## Status
- **Version:** v0.061.0
- **Tests:** 244 passed (214 original + 30 auto-backup)
- **Last commit:** `4035ab6` (auto-backup subsystem) on `dev`
