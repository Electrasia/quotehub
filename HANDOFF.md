# HANDOFF.md — Session Bridge

## Current Version

**v0.057.1** (dev branch)

---

## Last Completed Work

### v0.057.1 — SPA catch-all route
- UX: Navigating to any unmatched URL now serves the app (index.html) instead of raw JSON `{"detail":"Not Found"}`
- Chore: Added `GET /{path:path}` catch-all route in `backend/main.py` — no new files, no frontend changes
- Chore: Marked custom error pages done in Production Readiness Checklist

### v0.057.0 — Orphaned file cleanup
- Fix: `POST /remove-file` now deletes generated page images from `IMAGES_DIR/<stem>/` when removing a queue entry (orphan prevention)
- Fix: `POST /clear` now deletes all source files + page images from disk before clearing the in-memory list (orphan prevention)
- Fix: `POST /import/upload` now cleans up restored archive PDFs on failure — both empty quotations and all-items-empty paths (orphan prevention)
- Test: 189 tests total (185 + 4 new: remove-file image cleanup, clear disk cleanup, import orphan cleanup for both failure paths)
- Chore: Marked orphaned file cleanup done in Production Readiness Checklist; removed from Known Issues

### v0.056.0 — Login brute-force protection
- Feature: IP-based in-memory rate limiter on `/auth/login` — 5 failed attempts per 15-min sliding window returns HTTP 429, blocks for 15 min.
- Feature: `_get_client_ip()` helper respects X-Forwarded-For, X-Real-IP, and falls back to client.host / 127.0.0.1.
- Feature: `_check_rate_limit()` includes clock-jump guard (rejects timestamps >5 min in the future).
- Fix: Disabled-account logins (correct password + disabled user) do NOT count against the rate limit.
- Logging: Rate limit triggers logged with category AUTH and client IP.
- Doc: Noted that without a reverse proxy, Docker NAT makes this a global bucket.

### v0.055.3 — Rate limiting + bug fixes
- Feature: Upload queue capped at 50 pending files. Error message shows which user has the most pending files.
- Feature: `uploaded_by` field tracked per file entry for queue ownership visibility.
- Feature: Processing semaphore (`asyncio.Lock`) — only 1 file processed at a time across all users.
- Fix: `asyncio.wait_for(lock.acquire(), timeout=0)` **always raises TimeoutError** even when lock is free — Python cancels the coroutine before it runs. Replaced with `lock.locked()` check + direct `await lock.acquire()`.
- Fix: Lock try/finally indentation was wrong — `finally` at wrong level would have caused SyntaxError on startup.
- Fix: Double-click on search results not working when "Showing 10 most recent" message overwrites innerHTML (destroyed per-row `ondblclick` handlers). Replaced with event delegation on `#searchResults` container.
- Test: 56/56 non-async tests pass (29 extraction pipeline async tests have pytest-asyncio version mismatch — pre-existing environment issue).

### v0.055.2 — Lightweight schema migration system
- Feature: Added versioned schema migration system in `backend/db.py` (`_schema_version` table, `_run_migrations()`, `MIGRATIONS` dict)
- Feature: Empty `MIGRATIONS` dict ready for first real migration (supplier module)
- Doc: Added critical migration rules (DDL/DML separation, idempotent DML) in both HANDOFF.md and db.py source
- Chore: Marked Database migration system as done in Production Readiness Checklist
- Fix: Corrected "Persistent sessions" entry in Production Readiness Checklist (sessions are cookie-based, not in-memory — already working)
- Fix: Import endpoint now rejects entries with empty items array (prevents 0-item DB entries)
- Fix: Returns 400 error if ALL import entries have no items; reports skipped count for partial imports
- Fix: Frontend shows skipped count as warning in import results
- Fix: Cleaned 8 orphaned 0-item entries from database
- Fix: Deleted orphaned PDF from archive (`Electrasia211017-Commscope.pdf`)

### v0.055.0 — SQLite WAL mode
- Fix: Enabled WAL mode for concurrent reads without blocking

### v0.054.0 — Configurable upload size limit + SHA verification
- Feature: Configurable max upload size (1–20 MB, default 5 MB). Rejects oversized files before writing to disk, with clear error message.
- Feature: SHA256 checksum embedded in export ZIP (`quotations.json.sha256`). Import verifies it if present; backward compatible with older exports.
- Feature: Frontend settings field for upload limit (master-only, disabled for admin).
- Test: Added oversized file rejection test (85 total tests).

### v0.053.4 — Logging hardening + infrastructure
- Change: Added `logger.exception()`/`logger.warning()` to 41 silent `except Exception` blocks across 9 files — DB errors, PDF/XLSX parse failures, AI call errors, image gen failures, cleanup operations, VACUUM, upload state, and zip downloads now all visible in logs
- Change: Switched `deploy.sh` from raw `docker build/stop/rm/run` to single `docker compose up -d --build`
- Change: Added `container_name: quodb` to `docker-compose.yml` for consistent `docker exec` access
- Feature: Added `GET /health` endpoint returning `{"status": "ok"}` for Docker HEALTHCHECK (curls every 30s)
- Feature: Added 42 mock-based extraction pipeline tests (`tests/test_extraction_pipeline.py`)
- Feature: Added health check endpoint test (`tests/test_health.py`)
- Chore: Added `curl` to Dockerfile, `pytest.ini` with `asyncio_mode = auto`

### v0.053.3 — XLSX extraction fix
- Fix: XLSX extraction — clean cell newlines before pipe-joining (fixes "Unit Price\n(HKD)" splitting across multiple lines)
- Fix: XLSX extraction — increase text limit from 8K to 24K chars per sheet (was truncating large quotations)
- Fix: XLSX extraction — process each sheet as separate LLM call (avoids token overflow when combining multiple sheets)
- Fix: XLSX extraction — increase max_tokens to 8192 for XLSX (PDF stays at 4096)
- Change: Extracted `_call_llm()` helper for single LLM calls

### v0.053.2 — Vision LLM PDF path + multi-page fix
- Fix: `pdf_path` was missing from parse result, so Vision LLM was never called for scanned PDFs — extraction silently fell to local rules (returned nothing)
- Fix: Multi-page Vision LLM confusion — removed separate page 2 prompt; same prompt used for all pages with "leave empty on continuation pages" guidance

### v0.053.1 — SSE crash fix + document_type prompt
- Fix: SSE stream crash after extraction — removed `result.llm_warnings` reference that didn't exist on `ExtractionResult` dataclass (caused "Network error" on frontend)
- Fix: Added `document_type` to Vision and Text LLM prompts so model identifies QUO/PO/PL instead of defaulting to "unknown"

### v0.053.0 — Vision LLM + KISS Simplification
- Feature: Vision LLM integration — analyzes PDF images for scanned documents
- Feature: Auto extraction mode — 6 modes collapsed into 1; detects scanned vs text PDF vs XLSX
- Feature: `extraction_enabled` ON/OFF toggle replaces mode dropdown in settings
- Change: Vision LLM prompt simplified to ~15 lines; removed post-processing (`_norm_price`, `_validate_items`)
- Change: Text LLM prompt simplified to ~15 lines; removed field mapping, few-shot examples, header detection (~460 lines removed)
- Change: Fixed DPI at 200 (removed configurable `llm_dpi`)
- Fix: `normalize_date()` handles DD-MM-YYYY, DD-Mon-YYYY, MM/DD/YYYY, YYYY-MM-DD with dayfirst preference
- Fix: Brand defaults to empty string (not "unknown") when no brand column exists
- Fix: Prices returned as raw numbers — no more comma-stripping corruption

### v0.052.3 — LLM Extraction Improvements
- Feature: hybrid column detection (header row + content-based inference)
- Feature: post-processing validation for extracted items
- Feature: quantity/unit splitting (e.g., "3800 pcs" → qty=3800, unit=pcs)
- Feature: category headers automatically filtered out
- Feature: few-shot examples for valid items, headers, and work items
- Fix: OCR prompt now captures ALL columns including Price/Total
- Fix: LLM prompt handles PO format (no unit price required)

### v0.052.2 — XLSX Preview + Review Cancel
- Feature: XLSX preview renders as interactive read-only HTML table via SheetJS
- Feature: XLSX New Window opens spreadsheet viewer (auto-sized columns, sheet tabs)
- Feature: leaving review without saving shows "Review cancelled" status
- Fix: XLSX preview — auto-sized columns, cell borders, header styling
- Fix: trim empty trailing columns in XLSX preview
- Fix: text wraps within cells instead of spilling into adjacent columns
- Fix: archive endpoint serves correct MIME type per file extension
- Fix: New Window button uses original filename (works for PDF, XLSX, etc.)
- Fix: after cancelling review, always returns to step 1 (upload page)

### v0.052.0 — Search Enhancements + Validation
- Feature: document type filter dropdown (ALL/PO/QUO/PL) on search page
- Feature: auto-search on dropdown change
- Feature: limit empty search to 10 most recent documents
- Feature: require at least one item before saving quotation
- Fix: supplier now searchable in item-level filtering
- Fix: prevent layout shift on search dropdown toggle
- Fix: replace signout icon with text for cross-system compatibility
- Fix: save button now correctly enables after items loaded

### v0.051.1 — Minor Fixes
- Fix: pydantic protected_namespaces warning on ProcessRequest
- Fix: add delay in deploy.sh before reading init password

### v0.051.0 — Bug Fixes + Deploy Improvement
- Fix: upload error banner persists after Clear All
- Fix: must_change_password flag not cleared after password change (backend + frontend)
- Fix: users table missing on fresh Docker installs (init_db now creates it)
- Feature: deploy.sh shows initial master password after fresh install

---

## Files Changed Recently

### v0.057.1
- `backend/main.py` — Added `GET /{path:path}` catch-all route serving index.html for unmatched URLs
- `VERSION` — 0.057.0 → 0.057.1
- `CHANGELOG.md` — Added v0.057.1 release notes
- `HANDOFF.md` — Marked custom error pages done; updated version

### v0.057.0
- `backend/routes/files.py` — `remove-file`: image cleanup after source deletion; `clear`: file + image cleanup before list clear; `import/upload`: track restored PDFs, clean up on failure (orphan prevention for all 3 paths)
- `VERSION` — 0.056.0 → 0.057.0
- `CHANGELOG.md` — Added v0.057.0 release notes
- `HANDOFF.md` — Marked orphaned file cleanup done; updated test count to 189; removed orphan issues from Known Issues
- `tests/test_files_crud.py` — Added `TestRemoveFileCleanup` (image cleanup test) and `TestClearCleanup` (disk cleanup test)
- `tests/test_export_import.py` — Added `test_import_orphan_cleanup_empty_quotations` and `test_import_orphan_cleanup_all_skipped`

### v0.056.0
- `backend/routes/auth.py` — Added IP-based rate limiter: `_FAILED_LOGINS` dict, `_get_client_ip()`, `_check_rate_limit()`, guard in login route, failure recording, success clearing, clock-jump guard
- `VERSION` — 0.055.3 → 0.056.0
- `HANDOFF.md` — Marked brute-force protection done; added v0.056.0 changelog

### v0.055.3
- `backend/main.py` — Added `process_lock = asyncio.Lock()` for processing semaphore
- `backend/routes/files.py` — Upload queue cap (50 files) with owner-aware error message; `uploaded_by` per file entry; processing semaphore via `asyncio.Lock` with non-blocking acquisition; lock release in `finally` block
- `VERSION` — 0.055.2 → 0.055.3
- `HANDOFF.md` — Updated checklist, test count, next session

### v0.055.2
- `backend/db.py` — Added schema migration system (`_schema_version` table, `_init_schema_version`, `_get_schema_version`, `_run_migrations`, `MIGRATIONS` dict) with critical rules embedded as comments
- `HANDOFF.md` — Added Migration System section with critical rules; updated checklist and version

### v0.055.1
- `backend/routes/files.py` — Import validation: skip 0-item entries, return error if all invalid, report skipped count
- `frontend/js/settings.js` — Display skipped count in import results

### v0.054.0
- `backend/routes/files.py` — Upload size limit check (reject oversized files before write); SHA256 checksum on export; SHA verification on import; integrity warning in response
- `backend/utils.py` — Added `max_upload_size_mb: 5` to `_CONFIG_DEFAULTS`
- `backend/routes/admin.py` — Added validation rule for `max_upload_size_mb` (int 1–20)
- `frontend/index.html` — Added "Max Upload Size (MB)" input with `master-only` class
- `frontend/js/settings.js` — Save/load `max_upload_size_mb`; display integrity warning on import
- `frontend/js/nav.js` — Populate `settingsMaxUploadSizeMb` from config
- `frontend/js/auth.js` — Add `settingsMaxUploadSizeMb` to admin lock list
- `config.example.json` — Added `"max_upload_size_mb": 5`
- `.gitignore` — Added confidential test file patterns

### v0.053.4
- `backend/parser.py` — XLSX cell newline cleaning, 24K char limit per sheet; +12 `logger.exception/warning` calls
- `backend/ocr.py` — +8 `logger.exception/warning` calls for OCR/tesseract failures
- `backend/main.py` — +2 `logger.warning` calls for upload state load/save
- `backend/db.py` — +1 `logger.exception` call for DB rollback
- `backend/routes/files.py` — +9 `logger.warning` calls for page count/image gen failures; added `pdf_path` to parse result
- `backend/routes/admin.py` — +2 logging calls for cleanup/VACUUM; removed `extraction_mode`/`llm_dpi` validation
- `backend/routes/ai.py` — +1 `logger.warning` for AI status check
- `backend/extraction/vision.py` — +2 logging calls for AI retry/outer loop failures
- `backend/extraction/llm.py` — +1 `logger.warning` for AI retry failure
- `backend/extraction/router.py` — Single auto mode router; auto-detects scanned vs text vs XLSX
- `backend/extraction/__init__.py` — Updated exports
- `backend/utils.py` — Added `normalize_date()`, removed `extraction_mode` defaults
- `frontend/index.html` — 6-mode dropdown → ON/OFF AI toggle; OCR settings preserved
- `frontend/js/settings.js` — Removed mode/DPI from save; added `extraction_enabled` checkbox
- `frontend/js/nav.js` — Removed extraction mode badge function
- `tests/test_extraction_pipeline.py` — 42 mock-based extraction tests (router, LLM calls, normalize, clean item, integration)
- `tests/test_health.py` — Health check endpoint test
- `tests/conftest.py` — Updated fixture config
- `pytest.ini` — Added with `asyncio_mode = auto`
- `deploy.sh` — Switched to `docker compose up -d --build`
- `docker-compose.yml` — Added `container_name: quodb`, `healthcheck`
- `Dockerfile` — Added `curl` package

---

## Current Status vs SPEC.md

| Feature | Status |
|---------|--------|
| Upload & Process | ✅ Complete |
| Review & Edit | ✅ Complete |
| Search | ✅ Complete |
| Settings | ✅ Complete (simplified AI ON/OFF toggle) |
| Authentication & Roles | ✅ Complete |
| Export/Import | ✅ Complete (with 0-item validation) |
| System Cleanup | ✅ Complete |
| Config Validation | ✅ Complete |
| Automated Tests | ✅ **189 tests passing**. All endpoint categories covered: auth (35), search (12), admin (21), files CRUD (18), export/import (14), SSE error paths (4), health (1), extraction pipeline (44), upload validation (6). Full coverage across auth gates, CRUD operations, error paths, and disk cleanup. |
| Vision LLM (scanned PDFs) | ✅ Working (fixed pdf_path bug) |
| Multi-page PDF extraction | ✅ Working (single prompt for all pages) |

---

## Migration System

A lightweight versioned migration system lives in `backend/db.py`. It tracks schema version in a `_schema_version` table (single row, one integer). On startup, `init_db()` creates base tables, then runs any pending migrations in version order.

### ⚠️ CRITICAL RULES (do not ignore)

**Rule 1: DDL and DML in separate migration functions**

DDL (`CREATE TABLE`, `ALTER TABLE`, `DROP TABLE`) auto-commits in SQLite. If a single migration function mixes DDL and DML, and the DML fails partway, the DDL is already committed but the version is not updated. On retry, DDL is a no-op but DML may duplicate data.

✅ Correct — split into two versioned functions:
```python
# v1a: DDL only
# v1b: DML only
```

❌ Wrong — mixed in one:
```python
# v1: DDL + DML together  ← BAD
```

**Rule 2: DML must be idempotent**

Every DML operation (`INSERT`, `UPDATE`) in a migration must be safe to run multiple times. Use `INSERT OR IGNORE`, `SELECT ... WHERE NOT EXISTS`, or `UPDATE ... WHERE` with existence checks. Never use plain `INSERT` that would create duplicates on retry.

✅ Correct:
```python
db.execute("INSERT OR IGNORE INTO suppliers (name) VALUES (?)", (name,))
```

❌ Wrong:
```python
db.execute("INSERT INTO suppliers (name) VALUES (?)", (name,))  ← duplicates on retry
```

These rules are MANDATORY for every new migration. They prevent data corruption during startup failures or container restarts.

---

## Known Issues

- **XLSX viewer column resizing** — SheetJS renders a read-only HTML table; user cannot manually resize columns. Columns are auto-sized to fit content. To revisit: consider a library with built-in column resize support (e.g., ReoGrid, Luckysheet/Univer, or custom drag handlers with better event handling)
- **uploaded_by field not displayed in UI** — Each file entry stores `uploaded_by` (username) for queue ownership tracking, but the queue view (`upload.js:renderFileList()`) only shows filename/pages/status. If multi-user visibility is needed later, add an "Uploaded by" column to `renderFileList()` — the data is already there in `f.uploaded_by`.
- **Login brute-force protection** — ✅ Done (v0.056.0). IP-based rate limiter on `/auth/login`. See Security Gaps section for details and known limitations.
- **Orphaned file cleanup** — ✅ Done (v0.057.0). All three orphan sources fixed: remove-file images, clear files+images, import archive PDFs on failure.

---

## Security Gaps & Planned Fixes

### 🔴 Login brute-force protection (v0.056.0) ✅ DONE

**Current state:** `/auth/login` is protected by an IP-based in-memory rate limiter. After 5 failed attempts within a 15-minute sliding window, the IP is blocked for 15 minutes (HTTP 429). Successful login resets the counter. Rate limit triggers are logged.

**Key design:**
- Module-level dict in `backend/routes/auth.py`
- `_get_client_ip()` — respects X-Forwarded-For → X-Real-IP → client.host → 127.0.0.1 fallback
- `_check_rate_limit()` — prunes expired entries, includes clock-jump guard (5 min tolerance)
- Disabled-account logins (correct password) do NOT count as failed attempts
- In-memory only — state is lost on container restart (accepted tradeoff)
- No new dependencies, no DB writes, no frontend changes

**Known limitations (documented in source):**
- Without a reverse proxy in Docker, all clients share the Docker gateway IP, making this a global bucket
- Multi-worker uvicorn would fragment state across processes (current config uses 1 worker)
- IP rotation by attacker is not prevented (each IP gets independent budget)
- Malformed requests return 422 before the rate limiter runs (negligible CPU cost)

---

## Extraction Pipeline Reference

- **Scanned PDF** (avg text chars < 50/page) → Vision LLM (page-by-page image analysis, 200 DPI)
- **Text PDF** (avg text chars >= 50/page) → Text LLM (all pages combined, max_tokens: 4096)
- **XLSX** → Text LLM (each sheet processed separately, max_tokens: 8192)
- **Any fail** → Local rules fallback
- **AI disabled** (`extraction_enabled: false`) → Local rules only

---

## Production Readiness Checklist

Items still needed before the app can be considered production-ready:

| Priority | Item | Effort | Notes |
|----------|------|--------|-------|
| 🔴 High | **Persistent sessions** | 1 day | ✅ Already working (cookie-based). Starlette stores session data in signed cookies (client-side), not server memory. SECRET_KEY persists in data volume. Container restarts do NOT log users out. See `backend/main.py:275-281` and `backend/middleware.py`. |
| 🔴 High | **Database migration system** | 2 days | ✅ Lightweight versioned system (v0.055.2). Tracks schema version in `_schema_version` table. See Migration System section above for critical rules. |
| 🔴 High | **Login brute-force protection** | 1 hour | ✅ Done (v0.056.0). IP-based in-memory rate limiter. See Security Gaps section for details.
| 🟡 Medium | **SQLite WAL mode** | 1 line | ✅ Done (v0.055.0). Enables concurrent reads without blocking. |
| 🟡 Medium | **Expand test coverage** | 3 days | ✅ **189 tests across all endpoints** — auth (35), search (12), admin (21), files CRUD (18), export/import (14), SSE (4), health (1), extraction pipeline (44), upload validation (6). All auth gates, error paths, CRUD operations, and disk cleanup scenarios covered. |
| 🟡 Medium | **Rate limiting on upload & processing** | 0.5 day | ✅ Done (v0.055.3). Queue cap at 50 pending files. Processing semaphore (1 file at a time). 
| 🟢 Low | **HTTPS via reverse proxy** | 1 day | App runs HTTP only. For production, put behind nginx/Caddy with Let's Encrypt. |
| 🟢 Low | **Orphaned file cleanup** | 0.5 day | ✅ Done (v0.057.0). Three fixes: `remove-file` cleans images, `clear` cleans files+images, `import/upload` cleans restored PDFs on failure. No more orphans created in normal use. |
| 🟢 Low | **Custom error pages** | 0.5 day | ✅ Done (v0.057.1). SPA catch-all route serves the app UI for any unmatched URL instead of raw JSON 404. |
| 🟢 Low | **XLSX column resizing** | 2 days | Documented in Known Issues. SheetJS renders read-only table; users cannot resize columns. |

---

## Next Session Start Here

1. Review this HANDOFF.md for context
2. Check `git log --oneline -10` for any commits since this session
3. Run `pytest tests/ -v` to verify all tests pass (189 expected)
4. Remaining Production Readiness items (🟢 Low priority):
   - **HTTPS via reverse proxy** — App runs HTTP only. For production, put behind nginx/Caddy with Let's Encrypt.
   - **XLSX column resizing** — SheetJS renders read-only table; users cannot resize columns.
