# CHANGELOG.md — QuoteHub Release Notes

## v0.063.0 (2026-06-20)
- **Security**: Busy timeout — added `timeout=5` to `sqlite3.connect()` in `db.py` to prevent `database is locked` errors under concurrent writes
- **Security**: Path traversal prevention — `/upload` rejects filenames containing `..`, `/`, `\`
- **Security**: Magic bytes validation — `/upload` checks `%PDF` / `PK\x03\x04` before writing to disk
- **Security**: File-at-rest encryption — AES-256-GCM on write, transparent decrypt on read, keyed by `FILE_ENCRYPTION_KEY` env var
- **Security**: Non-root container — `quodb` user (UID 1001), `gosu` privilege drop via entrypoint, startup `chown` of `/app/data` volume for existing deployments
- **Decision**: Database at rest encryption accepted as risk — SQLite has no built-in encryption; SQLCipher would break the KISS deployment model. Protected by Docker volume isolation + filesystem permissions + network isolation on LAN behind NPM reverse proxy
- **Chore**: 29 new tests (15 upload validation + 14 encryption at rest) — 273 total tests
- **Chore**: VERSION → 0.063.0

## v0.062.0 (2026-06-20)
- **Feature**: Auto-backup subsystem — daily backups at 03:00, weekly promotions (Sunday), event-based backups (pre-update, pre-import, pre-bulk)
- **Feature**: Internal Backup Key manager — 2-layer HKDF-wrapped AES-256-GCM key hierarchy, machine-bound, key rotation via CLI
- **Feature**: Automatic backup retention — 7 daily / 4 weekly / 45-day event retention, purge-unused key versions
- **Feature**: Startup catch-up — missed daily backups are run on container restart
- **Feature**: Post-upgrade forensic check — logs warning if pre-update backup is missing after version change
- **Feature**: Frontend auto-backup display — status section in Settings showing last/next backup, success/failure indicator
- **Feature**: Frontend auto-restore modal — browse daily/weekly/event backups, dry-run preview, confirm & restore
- **Feature**: CLI — `python -m backend.cli backup pre-update --version X.Y.Z`, `key rotate`, `key current`
- **Security**: Export and import restricted to master role only (`require_role("master")`)
- **Security**: Password strengthening — eye icons, strength meters, 12-character minimum across all password forms
- **UX**: Success popups on user create/edit and password change
- **Chore**: Dead code removal from `backend/routes/auto_backup.py`
- **Chore**: 36 new auto-backup tests (startup catch-up, weekly promotion, retention sweep, CLI, unit, API)
- **Chore**: 221 total tests (36 auto-backup, 37 auth, 24 export/import unit, 21 admin, 20 extract, 18 files CRUD, 16 config, 13 pipeline, 13 export/import API, 12 search, 6 upload, 4 SSE, 1 health)
- **Chore**: VERSION → 0.062.0

## v0.061.0 (2026-06-19)
- **Change**: Removed all export password management (set/change/forgot) — password is now per-file, never stored. Matches the 7-Zip/KeePass/Veracrypt model.
- **Change**: Removed `POST /export-password` and `GET /export-password/status` endpoints — routes reduced from 47 to 45
- **Change**: `run_export(password, user)` signature accepts user dict for manifest attribution — no more stored hash check
- **Change**: Import response now includes `exportAttribution` (master identity) for import confirmation screen
- **Feature**: Silent decrypt round-trip after every export verifies the password before serving the download
- **Feature**: Frontend — Export modal reworked: warning banner + password+confirm + eye icons + strength bar, 3 states (input/progress/result)
- **Feature**: Frontend — Import has eye icon on password field, dry-run default unchecked, attribution display area
- **Chore**: Removed `get_master_user()` from `backend/auth.py` (unused after forgot-password removal)
- **Chore**: Removed `export_password_set` fixture and `TEST_EXPORT_PASSWORD` constant from `tests/conftest.py`
- **Chore**: 37 tests for export/import (24 unit + 13 API) — password management tests removed, `master_client` replaces `export_password_set`
- **Chore**: VERSION → 0.061.0

## v0.060.0 (2026-06-19)
- **Security**: Removed unencrypted `GET /export` (plain ZIP) — the only export path is now encrypted AES-256-GCM
- **Security**: Removed `POST /import/upload` (plain ZIP/JSON) — all imports go through the encrypted `.quodb` flow
- **Feature**: New `backend/export_import.py` — AES-256-GCM encrypted package format, PBKDF2-600K key derivation, streaming I/O for large files
- **Feature**: `POST /export-password` — set/change/forgot-recovery for export password (master-only, bcrypt stored)
- **Feature**: `GET /export-password/status` — check if export password is set (admin+)
- **Feature**: `POST /export/run` — encrypted `.quodb` export with DB snapshot, file verification, integrity check (admin+)
- **Feature**: `POST /import/run` — encrypted `.quodb` import with dry-run, dedup, system-ID check, file conflict detection, transactional apply (admin+)
- **Feature**: Frontend — Export Password modal, encrypted export/import UI, .quodb file picker (frontend/index.html, frontend/js/settings.js, frontend/js/nav.js)
- **Chore**: `backend/routes/export_import.py` — 4 new endpoints delegating to `export_import.py`
- **Chore**: `backend/routes/files.py` — removed 170 lines of dead export/import code + cleaned up dead imports (`hashlib`, `zipfile`, `tempfile`)
- **Chore**: `backend/main.py` — registered export/import router + 10 structured log keys
- **Chore**: `backend/db.py` — migration v1 creates `export_registry` table (already present)
- **Chore**: `backend/requirements.txt` — added `cryptography>=41.0.0`
- **Chore**: `tests/test_export_import_unit.py` — 31 new unit tests (password validation, crypto, record_hash, password management)
- **Chore**: `tests/test_export_import_api.py` — 26 new integration tests (auth gates, endpoints, round-trip)
- **Chore**: `tests/conftest.py` — added `export_password_set`, `with_archive_files` fixtures + `TEST_EXPORT_PASSWORD` constant
- **Fix**: `cryptography>=49` compatibility — `encryptor.finalize()` no longer returns GCM tag, use `encryptor.tag` instead
- **Fix**: PBKDF2 iterations now read at call time so monkeypatch/patch can override for fast tests
- **Chore**: VERSION → 0.060.0

## v0.059.1 (2026-06-18)
- UX: Queue now shows who uploaded each file — `by username` next to filename in `renderFileList()`
- Chore: `frontend/js/upload.js` — added `uploaded_by` display to file-item template
- Chore: VERSION → 0.059.1

## v0.059.0 (2026-06-18)
- Feature: `POST /cleanup/purge-orphans` endpoint — deletes orphan temp files (no queue entry) and orphan image directories (no reference in queue, archive, or DB)
- Feature: `GET /cleanup/stats` now reports `temp_file_count`, `temp_orphan_count`, `image_orphan_count`, and estimated bytes for orphan cleanup
- Chore: `backend/routes/admin.py` — added purge-orphans endpoint + extended stats with orphan reporting
- Chore: VERSION → 0.059.0

## v0.058.1 (2026-06-18)
- Fix: `save_upload_state()` was never called — queue persistence was dead code
- Fix: `save_upload_state()` now saves `uploaded_by` so uploader survives restart
- Fix: Frontend now restores queue from backend on page load via `GET /queue` — restored files no longer disappear on page refresh
- Feature: `GET /queue` endpoint returns the current upload queue
- Chore: `backend/routes/files.py` — calls `save_upload_state()` after upload, clear, remove-file, confirm, and skip
- Chore: `frontend/js/app.js` — `loadQueueState()` fetches and normalizes queue on init
- Chore: VERSION → 0.058.1

## v0.058.0 (2026-06-18)
- Feature: `trust_proxy_headers` config flag for Nginx Proxy Manager deployment
- Feature: `_get_client_ip()` no longer trusts proxy headers by default — fixes IP-spoofing vulnerability in dev
- Feature: `SecureCookieMiddleware` adds `Secure` flag to session cookie when behind HTTPS proxy
- Chore: `backend/utils.py` — added `trust_proxy_headers: False` to `_CONFIG_DEFAULTS`
- Chore: `backend/routes/auth.py` — guarded `_get_client_ip()` with config check
- Chore: `backend/middleware.py` — added `SecureCookieMiddleware` class
- Chore: `backend/main.py` — registered `SecureCookieMiddleware` in middleware stack
- Chore: `config.example.json` — added `trust_proxy_headers` placeholder
- Chore: `NPM-DEPLOY.md` — deployment guide for IT team (gitignored)
- Chore: VERSION → 0.058.0

## v0.057.2 (2026-06-17)
- UX: "✓ Ready to review" files in the queue are now clickable — tapping re-opens the review screen with all extracted data intact
- UX: After cancelling or saving from review, the app now routes to the file queue if files remain, instead of always jumping back to the upload page
- UX: Returning to the Process view from Search/Settings now lands on the queue if files exist
- Fix: Page preview images no longer go blank after cancelling and re-processing a file (stale page image directory cleaned up before regeneration)
- Fix: Preview no longer shows blank on cached images when re-entering review — step-4 panel now becomes visible before the image source is set, so autofit computes against the real container width
- Fix: Page preview shows a fallback message instead of a blank white box when images are genuinely unavailable
- Chore: `backend/routes/files.py` — clean stale image dir before `_generate_page_images()` on re-process
- Chore: `frontend/js/review.js` — moved `goToStep(4)` before `updateReviewPdf()` in `showReview()`
- Chore: `frontend/js/review.js` — conditional routing in `backToUpload()`
- Chore: `frontend/js/nav.js` — conditional routing in `showUpload()`
- Chore: `frontend/js/upload.js` — `done` files are clickable; `reviewDoneFile()` restores extracted data + page images
- Chore: `frontend/js/progress.js` — store `extractedData` per file entry for review re-entry

## v0.057.1 (2026-06-17)
- UX: SPA catch-all route — navigating to any unmatched URL now serves the app instead of raw JSON `{"detail":"Not Found"}`
- No new files, no frontend changes, no API changes

## v0.057.0 (2026-06-17)
- **Orphaned file cleanup** — 3 fixes covering all orphan sources:
  - `POST /remove-file` now deletes generated page images from `IMAGES_DIR/<stem>/` when removing a queue entry
  - `POST /clear` now deletes all source files + page images from disk before clearing the in-memory list
  - `POST /import/upload` now cleans up restored archive PDFs on failure (empty quotations or all items empty)
- Test: 189 tests total (185 + 4 new for orphan cleanup). All endpoint categories covered: auth, search, admin, files CRUD, export/import, SSE streaming, health, extraction pipeline, upload validation.

## v0.056.0 (2026-06-16)
- Feature: IP-based in-memory rate limiter on `/auth/login` — 5 failed attempts per 15-min sliding window returns HTTP 429, blocks for 15 min.
- Feature: `_get_client_ip()` helper respects X-Forwarded-For, X-Real-IP, and falls back to client.host / 127.0.0.1.
- Feature: `_check_rate_limit()` includes clock-jump guard (rejects timestamps >5 min in the future).
- Fix: Disabled-account logins (correct password + disabled user) do NOT count against the rate limit.
- Logging: Rate limit triggers logged with category AUTH and client IP.
- Doc: Noted that without a reverse proxy, Docker NAT makes this a global bucket.

## v0.054.0 (2026-06-15)
- Feature: Configurable max upload size limit (1–20 MB, default 5 MB). Files exceeding the limit are rejected with a clear error message before being written to disk.
- Feature: SHA256 checksum embedded in export ZIP (`quotations.json.sha256`). On import, checksum is verified if present (backward compatible — older exports without checksum still import successfully).
- Feature: Frontend settings field for upload limit, visible only to Master role (disabled for Admin).
- Test: Added oversized file rejection test (85 tests total).

## v0.053.4 (2026-06-15)
- Change: Added `logger.exception()`/`logger.warning()` to 41 silent `except Exception` blocks across 9 files

## v0.053.3 (2026-06-15)
- Fix: XLSX extraction — clean cell newlines before pipe-joining (fixes "Unit Price\n(HKD)" splitting across multiple lines)
- Fix: XLSX extraction — increase text limit from 8K to 24K chars per sheet (was truncating large quotations)
- Fix: XLSX extraction — process each sheet as separate LLM call (avoids token overflow when combining multiple sheets)
- Fix: XLSX extraction — increase max_tokens to 8192 for XLSX (PDF stays at 4096)
- Change: Extracted `_call_llm()` helper for single LLM calls

## v0.053.2 (2026-06-14)
- Fix: Vision LLM never called for scanned PDFs — `pdf_path` was missing from parser result, so router always fell through to local extraction (returned nothing)
- Fix: Multi-page Vision LLM confusion — removed separate page 2 prompt; same prompt used for all pages with "leave empty on continuation pages" guidance

## v0.053.1 (2026-06-14)
- Fix: SSE stream crash after extraction — removed `result.llm_warnings` reference that didn't exist on `ExtractionResult` dataclass (caused "Network error" on frontend)
- Fix: Added `document_type` to Vision and Text LLM prompts so model identifies QUO/PO/PL instead of defaulting to "unknown"

## v0.053.0 (2026-06-14)
- Feature: Vision LLM integration — analyzes PDF images for scanned documents
- Feature: Auto extraction mode — 6 modes collapsed into 1; detects scanned vs text PDF vs XLSX
- Feature: `extraction_enabled` ON/OFF toggle replaces mode dropdown in settings
- Change: Vision LLM prompt simplified to ~15 lines; removed post-processing (`_norm_price`, `_validate_items`)
- Change: Text LLM prompt simplified to ~15 lines; removed field mapping, few-shot examples, header detection (~460 lines removed)
- Change: Fixed DPI at 200 (removed configurable `llm_dpi`)
- Fix: `normalize_date()` handles DD-MM-YYYY, DD-Mon-YYYY, MM/DD/YYYY, YYYY-MM-DD with dayfirst preference
- Fix: Brand defaults to empty string (not "unknown") when no brand column exists
- Fix: Prices returned as raw numbers — no more comma-stripping corruption

## v0.052.3 (2026-06-14)
- Feature: hybrid column detection (header row + content-based inference)
- Feature: post-processing validation for extracted items
- Feature: quantity/unit splitting (e.g., "3800 pcs" → qty=3800, unit=pcs)
- Feature: category headers automatically filtered out
- Feature: few-shot examples for valid items, headers, and work items
- Fix: OCR prompt now captures ALL columns including Price/Total
- Fix: LLM prompt handles PO format (no unit price required)

## v0.052.2 (2026-06-14)
- Feature: XLSX preview renders as interactive read-only HTML table via SheetJS
- Feature: XLSX New Window opens spreadsheet viewer (auto-sized columns, sheet tabs)
- Feature: leaving review without saving shows "Review cancelled" status
- Fix: XLSX preview — auto-sized columns, cell borders, header styling
- Fix: trim empty trailing columns in XLSX preview
- Fix: text wraps within cells instead of spilling into adjacent columns
- Fix: archive endpoint serves correct MIME type per file extension
- Fix: New Window button uses original filename (works for PDF, XLSX, etc.)
- Fix: after cancelling review, always returns to step 1 (upload page)

## v0.052.0 (2026-06-14)
- Feature: document type filter dropdown (ALL/PO/QUO/PL) on search page
- Feature: auto-search on dropdown change
- Feature: limit empty search to 10 most recent documents
- Feature: require at least one item before saving quotation
- Fix: supplier now searchable in item-level filtering
- Fix: prevent layout shift on search dropdown toggle
- Fix: replace signout icon with text for cross-system compatibility
- Fix: save button now correctly enables after items loaded

## v0.051.1 (2026-06-14)
- Fix: pydantic protected_namespaces warning on ProcessRequest
- Fix: add delay in deploy.sh before reading init password

## v0.051.0 (2026-06-14)
- Fix: upload error banner persists after Clear All
- Fix: must_change_password flag not cleared after password change
- Fix: users table missing on fresh Docker installs
- Feature: deploy.sh shows initial master password

## v0.050.0 (2026-06-11)
- Config validation: timeout, retries, extraction_mode, endpoint URL, booleans
- Empty file upload rejection (backend + frontend validation)
- Config default consistency: extraction_mode uses get_config_data()
- Automated test suite: 41 tests (config, upload, extraction)
- config.example.json: extraction_mode key added, stale key removed

## v0.049.0 (2026-06-10)
- Admin role restrictions: hidden General, Extraction, Cleanup; Import disabled
- Documentation updates: README, Help view, stale debug references cleaned

## v0.048.0 (2026-06-10)
- Fix: Search sort for Date and Supplier columns
- Fix: Search delete with rowcount check and image dir cleanup
- Fix: Cleanup ImportError for DB_PATH
- Remove: Developer Tools feature (843 lines)

## v0.047.0 (2026-06-10)
- Fix: Cleanup uses quotation_date instead of created_at
- Feature: Document type filter (ALL/PO/QUO/PL) for cleanup
- Feature: Step 0 stats section in System Cleanup

## v0.046.0 (2026-06-09)
- Fix: Logs download and content capture
- Fix: Reupload after Clear All
- Fix: Navigation warning popup
- Feature: Extraction mode visual indicator
- Feature: Comprehensive logging system with categories

## v0.045.0 (2026-06-08)
- Feature: Session management with idle timeout
- Feature: Remember Me with custom middleware
- Fix: Settings save button response handling
- Fix: Extraction mode default to llm_first

## v0.044.0 (2026-06-08)
- Feature: Session idle timeout (15 minutes)
- Feature: Remember Me checkbox
- Feature: Settings reorganized into 3 sections

## v0.043.0 (2026-06-07)
- Fix: Confirm & Save input indices
- Fix: Search page date display
- Feature: Edit modal Excel styling

## v0.042.0 (2026-06-07)
- Feature: Review page enhancements
- Feature: PDF preview with zoom controls

## v0.041.0 (2026-06-07)
- Feature: Review page layout improvements
- Feature: Dynamic column widths

## v0.040.0 (2026-06-06)
- Feature: Date column hidden from items table
- Feature: Placeholders searchable via Find & Replace
