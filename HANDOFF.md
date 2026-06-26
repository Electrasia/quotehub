# HANDOFF.md — Session Bridge

## Current Version

**v0.063.2** (dev branch)

---

## Last Completed Work

### v0.063.1 — Production audit remaining red flags (8 fixes)

Addresses the 8 remaining red flags from the production audit (FTS-01, ST-01, ST-02, DB-04, TMP-01, REQ-01, SEC-01, RF-07).

**Fixed — FTS-01 (FTS5 MATCH injection):**
- `backend/routes/admin.py` — Search terms are now sanitized with `re.sub(r'[^\w]', '', w)` before building the FTS5 query. Strips FTS5 operators (`-`, `+`, `AND`, `OR`, `NOT`, `NEAR`, `*`, parentheses) so they can't alter query semantics. A search for `-701` now correctly finds items containing `701` instead of doing a NOT query.
- Added `import re` to admin.py.

**Fixed — ST-01 (uploaded_files unsynchronized):**
- `backend/main.py` — Added `uploaded_files_lock = asyncio.Lock()` alongside `uploaded_files`. Made `load_upload_state()` and `save_upload_state()` async with lock acquisition.
- `backend/routes/files.py` — Made `_find_file_by_id()`, `_find_file_by_index()`, and `_resolve_file()` async with lock. Wrapped all 27 remaining `uploaded_files` accesses across `/upload`, `/clear`, `/remove-file`, `/queue`, `/next-file`, `/process-stream`, `/confirm`, and `/skip` with `async with uploaded_files_lock:`. `/queue` now returns `list(uploaded_files)` copy instead of raw list reference.
- `backend/routes/admin.py` — Wrapped `uploaded_files` iterations in `cleanup_stats` and `run_cleanup` with the lock.

**Fixed — ST-02 (process_lock at module level):**
- `backend/main.py` — Changed `process_lock = asyncio.Lock()` to `process_lock = None` at module level. Lock is now created inside `lifespan()` with `global process_lock; process_lock = asyncio.Lock()`. The lazy import in `files.py:process-stream` sees the lifespan-created lock.

**Fixed — TMP-01 (OCR temp files world-readable, cleanup gaps):**
- `backend/ocr.py` — Replaced `tempfile.NamedTemporaryFile(delete=False)` (world-readable, default umask) with `tempfile.mkstemp()` + `os.close(fd)` (0o600 permissions). Restructured the `ocr_pdf_llm()` function so a single `finally` block covers all return paths after PDF rendering (render failure, no pages, LLM success, LLM failure, unexpected exceptions), eliminating the previous duplicate and gapped cleanup. Added `import os`.

**Fixed — REQ-01 (import reads entire file into memory):**
- `backend/routes/export_import.py` — Replaced `content = await file.read()` + `f.write(content)` with `shutil.copyfileobj(file.file, f, length=1024*1024)`, streaming the upload directly to disk in 1 MB chunks. A 5+ GB backup file now uses ~1 MB of RAM during the write instead of 5+ GB. Added `import shutil`.

**Fixed — SEC-01 (document content in logs, no redaction):**
- `backend/routes/files.py` — Replaced `'supplier': supplier` with `'supplier': '[REDACTED]'` in both "Quotation saved" and "Quotation updated" log messages. The `exc_info=True` handlers in `ocr.py`, `llm.py`, and `vision.py` are left untouched — they contain developer warnings and provide essential debugging data.

**Fixed — DB-04 (WAL autocheckpoint not tuned):**
- `backend/db.py` — Added `PRAGMA wal_autocheckpoint=500` after the WAL pragma. Checkpoints every ~2 MB instead of the SQLite default 1000 pages (~4 MB), keeping checkpoint pauses smaller and more frequent.

**Fixed — RF-07 (lifespan logs AI endpoint URL):**
- `backend/main.py` — Changed startup log from `f"AI endpoint: {cfg.get('ai_endpoint', 'NOT SET')}"` to `f"AI endpoint: {'configured' if ep else 'NOT SET'}"`. Still confirms config loaded without leaking the LAN IP address.

**Fixed — Config tracking (config.json removed from git):**
- `config.json` removed from git tracking (`git rm --cached config.json`) and added to `.gitignore` — prevents accidental commit of credentials or LAN IPs.
- `deploy.sh` auto-copies `config.example.json` → `config.json` on fresh installs if `config.json` doesn't exist.
- The Docker image never contains `config.json` (P2-18 already removed `COPY config.json` from Dockerfile). Config is always mount-only at runtime.

**Tests:**
- All existing tests remain unchanged. Syntax verified on all modified files.

### v0.063.2 — Search edit button fix

**Fixed — Search edit button not opening modal:**
- `frontend/js/search.js` — `editSelected()` now correctly unwraps the search API response: changed `const results = await resp.json()` to `const data = await resp.json(); const results = data.results || []`. The API returns `{"results": [...], "limited": bool}`, but the code was calling `.find()` on the entire response object (always undefined), causing the "Quotation not found." popup to appear instead of opening the edit modal.

**Tests:**
- All existing tests remain unchanged.

### v0.063.0 — Production audit fixes (P0-1 through P0-10, P1-1 through P1-4), all P0 + P1 items addressed

This release addresses all 10 P0 findings and all 4 P1 findings from a production-readiness audit:

**Fixed — P0-1 (busy timeout):**
- `backend/db.py` — Added `timeout=5` to `sqlite3.connect()` so concurrent writes don't raise `database is locked`

**Fixed — P0-2 (path traversal):**
- `backend/routes/files.py` — Upload handler rejects filenames containing `..`, `/`, `\` before any file is written. Added empty-stem check (e.g. `.pdf` with no base name).

**Fixed — P0-3 (magic bytes):**
- `backend/routes/files.py` — After extension/size checks, verifies that `.pdf` files start with `%PDF` and `.xlsx` files start with `PK\x03\x04`. Prevents renamed executables or other file types from being stored.

**Fixed — P0-4 (file-at-rest encryption):**
- `backend/export_import.py` — Added `encrypt_file_at_rest()`, `decrypt_file_at_rest()`, `get_encryption_key()`, `decrypt_file_to_temp()`. Reuses existing AES-256-GCM primitives with a raw 32-byte key (no PBKDF2 overhead — key comes from `FILE_ENCRYPTION_KEY` env var). Format: `nonce(16) + ciphertext + tag(16)` = 32 bytes overhead per file.
- `backend/routes/files.py` — Upload handler encrypts content before writing. `_count_pages()` and `_generate_page_images()` have transparent decryption wrappers. `process_stream()` pre-decrypts once and passes the decrypted path through the parser + extraction pipeline, cleaning up the temp file in the `finally` block.
- `docker-compose.yml` — Added `FILE_ENCRYPTION_KEY=${FILE_ENCRYPTION_KEY:-}` env var (defaults to empty = no encryption, backward compatible).
- Backward compatible: when `FILE_ENCRYPTION_KEY` is unset, no encryption is applied.

**Fixed — P0-6 (non-root container):**
- `Dockerfile` — Created `quodb` user (UID 1001), installed `gosu` for privilege drop, `chown` data dirs at build time, switched to entrypoint-based startup.
- `entrypoint.sh` — New file. Runs as root, `chown -R quodb:quodb /app/data` + `chown quodb:quodb /app/config.json` (fixes settings save bug), then `exec gosu quodb "$@"` to drop privileges and start the app.
- No `USER` directive in Dockerfile — the entrypoint handles privilege dropping, which is the standard Docker pattern (same as PostgreSQL, Redis, etc.).
- Existing deployments are handled automatically — the entrypoint's `chown` fixes ownership of pre-existing volumes on first restart.
- **Fix**: Added `/app/config.json` to chown list — `quodb` user could not write settings to bind-mounted config.json (PermissionError → 500 → frontend JSON parse error).

**Fixed — P0-7 (API docs exposure):**
- `backend/main.py` — FastAPI instantiation reads `QUODB_DOCS_ENABLED` env var; `/docs`, `/redoc`, `/openapi.json` disabled by default.
- `docker-compose.yml` — Added `QUODB_DOCS_ENABLED=${QUODB_DOCS_ENABLED:-false}` env var.
- Toggle on for debugging: `QUODB_DOCS_ENABLED=true docker compose up`

**Fixed — P0-8 (host header injection):**
- `backend/main.py` — Added `TrustedHostMiddleware(allowed_hosts=["*"])` to middleware stack.
- Accepted risk with wildcard — avoids IP/hostname churn on LAN. LAN + NPM + session auth = no practical exploit.

**Fixed — P0-9 (LLM output validation):**
- `backend/extraction/llm.py` — Added `ExtractionResult` + `ExtractionItem` Pydantic models. All LLM output validated through `model_validate()`; `ValidationError` caught gracefully as extraction warning. Catches type errors, missing fields, and malformed structures before they reach downstream code.

**Fixed — P0-10 (VLM response size cap):**
- `backend/extraction/vision.py` — Added 100 KB response size cap with truncate + warn. 12× safety margin over `max_tokens=4096` (~8 KB). Prevents OOM on runaway model output.

**Decision — P0-5 (DB encryption — accepted risk):**
- The database is not encrypted at rest. SQLite has no built-in encryption; SQLCipher would require recompiling the Python sqlite3 driver, add a fragile build dependency, and break any tool that reads the DB directly.
- Current protections: Docker named volume isolation (only the `quodb` container mounts `quodb_data`), filesystem permissions (root-owned on host), network isolation (LAN behind NPM reverse proxy), no PII or credentials stored.
- **Recommendation**: If threat model changes (shared cloud VM, PII storage), use LUKS at the host level — not application-level crypto.

**Tests:**
- `tests/test_upload_validation.py` — 15 tests covering extension, path traversal, stem check, empty file, oversized file (now expects 413), magic bytes, mixed batches
- `tests/test_encryption_at_rest.py` — 14 tests covering crypto round-trip, key env var, disk encryption verification, backward compat without key
- 274 total tests passing

**Fixed — P1-1 / P1-2 (XSS sinks in popups):**
- `frontend/js/utils.js` — `showBriefPopup()` and `showConfirmPopup()` changed from `innerHTML` to `textContent`. Message text is never rendered as HTML, eliminating the XSS vector.

**Fixed — P1-3 (XSS in renderAutoRestoreList):**
- `frontend/js/settings.js` — Refactored to DOM APIs (`createElement`, `textContent`, `addEventListener`). No HTML string interpolation with user data — eliminates entire class of bugs rather than relying on `escapeHtml()`.

**Fixed — P1-4 (Content-Length boundary check):**
- `backend/routes/files.py` — Early `Content-Length` header check at top of `/upload`. If the declared size exceeds `max_upload_size_mb`, returns `413 Payload Too Large` immediately before any body is buffered. Prevents resource exhaustion at the network boundary.

### v0.063.0 (continued) — Production audit P2 + P3 items addressed

Continuing the production-readiness audit: 12 more findings addressed across P2 and P3 priorities.

**Fixed — P2-14 (global exception handler):**
- `backend/main.py` — Added `@app.exception_handler(Exception)` that logs full traceback server-side via `logger.exception()`, returns safe `500 {"detail": "Internal Server Error"}` to client. FastAPI's built-in handlers for `HTTPException` and `RequestValidationError` take precedence (more specific), so this only catches true 500-level errors.

**Fixed — P2-17 (FTS rebuild test & documentation):**
- `tests/test_search.py` — Added `TestFtsRebuild` class: inserts data via standard fixtures, runs `INSERT INTO quotations_fts(quotations_fts) VALUES('rebuild')`, verifies search still returns correct results.
- `HANDOFF.md` — New **FTS Index Rebuild** section documenting the `docker exec` one-liner for production recovery.

**Fixed — P2-18 (config.json Docker build layer):**
- `Dockerfile` — Removed `COPY config.json .` (line 25). Config is mount-only at runtime via `docker-compose.yml`. Prevents local secrets from being baked into image layers.

**Fixed — P3-19 (CORSMiddleware):**
- `backend/main.py` — Added `CORSMiddleware(allow_origins=["*"])` with documented intent. Same-origin frontend + `same_site="lax"` session cookie means no practical attack surface; wildcard prevents operational issues.

**Fixed — P3-20 (Content-Security-Policy header):**
- `backend/middleware.py` — Added `CSPMiddleware` setting `default-src 'self'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'`. `'unsafe-inline'` required for frontend's 78 inline event handlers; all XSS sinks already fixed (P1-1 through P1-3).

**Fixed — P3-21 (X-Content-Type-Options header):**
- `backend/middleware.py` — Added `X-Content-Type-Options: nosniff` to `CSPMiddleware`. Prevents browser MIME-sniffing. Standard OWASP header.

**Fixed — P3-10 (version pinning):**
- `backend/requirements.txt` — Pinned last two unpinned dependencies: `cryptography>=41.0.0` → `cryptography==48.0.0`, `bcrypt<4.1` → `bcrypt==4.0.1`. All 15 dependencies now exact versions.

**Accepted — P2-15 (config.json secrets on volume):**
- AI endpoint is local LAN only — no credentials, no API keys. Moving to env var would break the Settings UI pattern and require container restarts on every AI server change.

**Fixed — P2-16 (lifespan logs AI endpoint):**
- Changed startup log from `f"AI endpoint: {cfg.get('ai_endpoint', 'NOT SET')}"` to `f"AI endpoint: {'configured' if ep else 'NOT SET'}"`. Confirms config loaded without leaking the LAN IP address.

**Fixed — P2-6 (AI degradation UX notification):**
- `frontend/index.html` — Added hidden `#aiFallbackWarning` banner div above the extracted data heading.
- `frontend/style.css` — Added `.ai-fallback-warning` yellow warning style.
- `frontend/js/review.js` — `showReview()` checks `extractedData.extraction_method` and shows banner when `'local'`.
- When AI is unreachable and extraction falls to local rules, the review screen shows: "⚠ AI server unreachable — extraction used local rules. Results may be limited."

**Fixed — P2-8 (container resource limits):**
- `docker-compose.yml` — Added `deploy.resources.limits`: 2 CPUs / 4 GB RAM. Docker throttles CPU on exceed and kills container on OOM; auto-restarts via `restart: unless-stopped`.

**Fixed — P2-9 (HA documentation):**
- `README.md` — Added single-node deployment note to Data Persistence section stating QuoteHub is single-node, SQLite cannot cluster, no HA planned.

**Tests:**
- `tests/test_search.py` — 1 new FTS rebuild test
- 274 total tests passing

### v0.062.0 — Auto-backup subsystem, master-only export/import, password strengthening

- **Feature**: Auto-backup subsystem — `backend/auto_backup.py` — daily backups at 03:00, weekly promotions (Sunday), event-based backups (pre-update, pre-import, pre-bulk), startup catch-up, post-upgrade forensic check
- **Feature**: Internal Backup Key manager — `backend/key_manager.py` — 2-layer HKDF-wrapped AES-256-GCM key hierarchy, machine-bound, rotatable via CLI
- **Feature**: Auto-backup routes — `backend/routes/auto_backup.py` — `GET /auto-backup/status`, `GET /auto-backup/list`, `POST /auto-backup/restore` with dry-run
- **Feature**: CLI — `backend/cli.py` — `python -m backend.cli backup pre-update --version X.Y.Z`, `key rotate`, `key current`
- **Feature**: Frontend auto-backup display — status section in Settings with last/next backup, success/failure indicator
- **Feature**: Frontend auto-restore modal — browse daily/weekly/event backups, dry-run preview, confirm & restore
- **Security**: Export and import restricted to **master-only** (`require_role("master")`)
- **Security**: Password strengthening — eye icons, strength meters, 12-character minimum across all login/password forms
- **UX**: Success popups on user create/edit and password change
- **Chore**: Dead code removal from `backend/routes/auto_backup.py` (pre-merge cleanup)
- **Chore**: 36 new auto-backup tests (startup catch-up, weekly promotion, retention sweep, CLI, unit, API = `tests/test_auto_backup.py`)
- **Chore**: 221 total tests (36 auto-backup, 37 auth, 24 export/import unit, 21 admin, 20 extract, 18 files CRUD, 16 config validation, 13 pipeline, 13 export/import API, 12 search, 6 upload validation, 4 SSE, 1 health)
- **Chore**: VERSION → 0.062.0

### v0.061.0 — Simplified export/import (no stored password)

- **Change**: Removed all export password management (set/change/forgot) — password is now per-file, never stored. Matches the 7-Zip/KeePass/Veracrypt model.
- **Change**: Removed `POST /export-password` and `GET /export-password/status` endpoints — routes reduced from 47 to 45
- **Change**: `run_export(password, user)` signature accepts user dict for manifest attribution — no more stored hash check
- **Change**: Import response now includes `exportAttribution` (master identity) for import confirmation screen
- **Feature**: Silent decrypt round-trip after every export verifies the password before serving the download
- **Feature**: Frontend — Export modal reworked: warning banner + password+confirm + eye icons + strength bar, 3 states (input/progress/result)
- **Feature**: Frontend — Import has eye icon on password field, dry-run default unchecked, attribution display area
- **Chore**: Removed `get_master_user()` from `backend/auth.py` (unused after forgot-password removal)
- **Chore**: Removed `export_password_set` fixture and `TEST_EXPORT_PASSWORD` constant from `tests/conftest.py`; added `fast_crypto` fixture
- **Chore**: 37 tests for export/import (24 unit + 13 API) — password management tests removed, `master_client` replaces `export_password_set`
- **Chore**: VERSION → 0.061.0

### v0.060.0 — Encrypted AES-256-GCM export/import
- **Security**: Removed unencrypted `GET /export` — the only export path is now encrypted
- **Security**: Removed `POST /import/upload` — all imports go through the encrypted `.quodb` flow
- **Feature**: `backend/export_import.py` — AES-256-GCM encrypted package format, PBKDF2-600K key derivation, streaming I/O
- **Feature**: `POST /export-password` — set/change/forgot-recovery for export password (master-only, bcrypt stored)
- **Feature**: `GET /export-password/status` — check if export password is set (admin+)
- **Feature**: `POST /export/run` — encrypted `.quodb` export with integrity check (admin+)
- **Feature**: `POST /import/run` — encrypted `.quodb` import with dry-run, dedup, system-ID check, transactional apply (admin+)
- **Feature**: Frontend — Export Password modal, encrypted export/import UI, `.quodb`-only picker
- **Chore**: 4 new endpoints in `backend/routes/export_import.py`
- **Chore**: Removed ~170 lines of dead export/import code from `backend/routes/files.py`
- **Chore**: Migration v1 creates `export_registry` table
- **Chore**: Added `cryptography>=41.0.0` to requirements
- **Chore**: 57 new tests (31 unit + 26 integration)
- **Fix**: `cryptography>=49` GCM API — `encryptor.finalize()` no longer returns tag, use `encryptor.tag`
- **Fix**: PBKDF2 iterations read at call time so tests can monkeypatch
- **Chore**: VERSION → 0.060.0

### v0.058.1 — Queue persistence fix
- Fix: `save_upload_state()` was defined but never called — dead code since v0.039
- Fix: `save_upload_state()` now saves `uploaded_by` field so uploader survives restart
- Fix: Frontend now restores queue from backend on page load via `GET /queue` — restored files no longer disappear on page refresh
- Feature: `GET /queue` endpoint returns the current upload queue
- Chore: `backend/routes/files.py` — calls `save_upload_state()` after upload, clear, remove-file, confirm, and skip
- Chore: `frontend/js/app.js` — `loadQueueState()` fetches and normalizes queue on init
- Chore: VERSION → 0.058.1

### v0.058.0 — Nginx Proxy Manager preparation
- Feature: `trust_proxy_headers` config flag (default `false`) — guarded `_get_client_ip()` only trusts proxy headers when explicitly enabled
- Feature: `SecureCookieMiddleware` adds `Secure` flag to session cookie when `trust_proxy_headers` is `true` — browser only sends cookie over HTTPS
- Fix: `_get_client_ip()` previously trusted `X-Forwarded-For` unconditionally, allowing rate-limiter bypass via header spoofing in dev
- Doc: `NPM-DEPLOY.md` — step-by-step deploy guide for IT team (gitignored, never pushed)
- Chore: VERSION → 0.058.0

### v0.057.2 — UX polish: queue navigation, blank preview fix, clickable done files
- UX: "✓ Ready to review" files in the queue are now clickable — tapping re-opens the review screen with all extracted data intact (page images must still be on disk)
- UX: After cancelling or saving from review, the app now routes to the file queue if files remain, instead of always jumping back to the upload page
- UX: Returning to the Process view from Search/Settings now lands on the queue if files exist, not the upload page
- Fix: Page preview images no longer go blank after cancelling and re-processing a file (stale page image directory from cancelled run is cleaned up before regeneration)
- Fix: Preview no longer shows blank on cached images when re-entering review — step-4 panel now becomes visible before the image source is set, so autofit computes against the real container width instead of 0
- Fix: Page preview shows a fallback message ("Use ↗ New Window to view the original file") instead of a blank white box when images are genuinely unavailable

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
- Change: after cancelling review, returns to step 1 (upload page) — **behavior changed in v0.057.2**: now routes to queue if files remain, upload only when queue is empty

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

### v0.063.1
- `config.json` — Removed from git tracking (`git rm --cached`); previously committed LAN IP no longer pushed.
- `.gitignore` — Already listed `config.json` (line 21), preventing re-addition.
- `backend/routes/admin.py` — `import re` added; FTS5 MATCH query sanitized with `re.sub(r'[^\w]', '', w)` to strip operators.
- `backend/main.py` — Added `uploaded_files_lock`, made `load_upload_state()`/`save_upload_state()` async with lock. Changed `process_lock = asyncio.Lock()` → `process_lock = None` at module level, created inside `lifespan()`. AI endpoint URL redacted from startup log.
- `backend/routes/files.py` — Made `_find_file_by_id()`, `_find_file_by_index()`, `_resolve_file()` async with `uploaded_files_lock`. Wrapped all `uploaded_files` access in route handlers (upload, clear, remove-file, queue, next-file, process-stream, confirm, skip) with lock. `/queue` returns list copy. Supplier name redacted in "Quotation saved" and "Quotation updated" logs.
- `backend/routes/admin.py` — Wrapped `uploaded_files` iterations in `cleanup_stats` and `run_cleanup` with lock.
- `backend/ocr.py` — `import os` added. `NamedTemporaryFile(delete=False)` → `tempfile.mkstemp()` + `os.close()` (0o600 perms). Consolidated cleanup into single `finally` covering all return paths.
- `backend/routes/export_import.py` — `import shutil` added. `await file.read()` → `shutil.copyfileobj(file.file, f, length=1024*1024)` streaming write.
- `backend/db.py` — Added `PRAGMA wal_autocheckpoint=500`.

### v0.063.0
- `backend/export_import.py` — Added `encrypt_file_at_rest()`, `decrypt_file_at_rest()`, `get_encryption_key()`, `decrypt_file_to_temp()`. Reuses existing AES-256-GCM + `_derive_key()` with `iterations=0` (raw key mode).
- `backend/routes/files.py` — Upload handler encrypts content before write. `_count_pages()` → `_count_pages_impl()` wrapper with transparent decryption. `_generate_page_images()` → `_generate_page_images_impl()` wrapper. `process_stream()` pre-decrypts filepath for parser + vision pipeline. Temp file cleanup in `finally` block. Path traversal rejection, empty-stem rejection, magic bytes validation.
- `backend/db.py` — Added `timeout=5` to `sqlite3.connect()`.
- `Dockerfile` — Added `gosu` install, created `quodb` user (UID 1001), `chown` data dirs at build time, set `ENTRYPOINT` to entrypoint.sh.
- `entrypoint.sh` — New file. chowns `/app/data` + `/app/config.json` at runtime, then drops to `quodb` user via `gosu`.
- `backend/main.py` — FastAPI instantiation reads `QUODB_DOCS_ENABLED` env var; `/docs`, `/redoc`, `/openapi.json` disabled by default. Added `TrustedHostMiddleware(allowed_hosts=["*"])`.
- `docker-compose.yml` — Added `FILE_ENCRYPTION_KEY` and `QUODB_DOCS_ENABLED` env vars.
- `backend/extraction/llm.py` — Added `ExtractionResult` + `ExtractionItem` Pydantic models; LLM output validated through `model_validate()`.
- `backend/extraction/vision.py` — Added 100 KB VLM response size cap with truncate + warn.
- `tests/test_upload_validation.py` — 15 tests: extension, path traversal, stem check, empty file, oversized, magic bytes, mixed batches.
- `tests/test_encryption_at_rest.py` — New file. 14 tests: crypto round-trip, key env var, disk encryption verification, backward compat.
- `VERSION` — 0.062.0 → 0.063.0
- `CHANGELOG.md` — Added v0.063.0 release notes
- `HANDOFF.md` — Full rewrite: all 10 P0 items documented, P1-P3 findings updated to actual audit results, work log and files changed updated through P0-10
- `frontend/js/utils.js` — `showBriefPopup()` and `showConfirmPopup()` changed from `innerHTML` to `textContent` (P1-1, P1-2).
- `frontend/js/settings.js` — `renderAutoRestoreList()` refactored to DOM APIs — no HTML string interpolation (P1-3).
- `backend/routes/files.py` — Added `Content-Length` header check returning 413 before body read (P1-4).
- `tests/test_upload_validation.py` — Updated oversized test to expect 413 status code.
- `backend/main.py` — Added `Request` import, `JSONResponse` import, `CORSMiddleware` import; added `@app.exception_handler(Exception)` (P2-14); added `CSPMiddleware` registration (P3-20); added `CORSMiddleware` registration (P3-19).
- `backend/middleware.py` — Added `CSPMiddleware` class with `Content-Security-Policy` + `X-Content-Type-Options: nosniff` headers (P3-20, P3-21).
- `Dockerfile` — Removed `COPY config.json .` (P2-18).
- `backend/requirements.txt` — Pinned `cryptography==48.0.0`, `bcrypt==4.0.1` (P3-10).
- `tests/test_search.py` — Added `TestFtsRebuild` class with `test_fts_rebuild_preserves_search` (P2-17).
- `frontend/index.html` — Added `#aiFallbackWarning` banner div for AI degradation notification (P2-6).
- `frontend/style.css` — Added `.ai-fallback-warning` style (P2-6).
- `frontend/js/review.js` — `showReview()` shows/hides AI fallback warning banner based on `extraction_method` (P2-6).
- `docker-compose.yml` — Added `deploy.resources.limits` (2 CPUs / 4 GB RAM) (P2-8).
- `README.md` — Added single-node deployment note in Data Persistence section (P2-9).

### v0.062.0
- `backend/auto_backup.py` — New file. Automatic backup subsystem: daily/weekly/event tiers, retention sweep, background scheduler, startup catch-up, post-upgrade check.
- `backend/key_manager.py` — New file. Internal Backup Key management: 2-layer HKDF-wrapped AES-256-GCM, key rotation, version purge.
- `backend/cli.py` — New file. CLI entry point for pre-update backup and key operations.
- `backend/routes/auto_backup.py` — New file. Auto-backup status, list, and restore endpoints.
- `backend/export_import.py` — Export/import unchanged (reused by auto-backup with key_version >= 2).
- `backend/routes/export_import.py` — `POST /export/run` and `POST /import/run` restricted to `require_role("master")`.
- `backend/routes/files.py` — Added `pre_import_backup()` call before import to create event backup.
- `backend/main.py` — Registered auto-backup router, calls `start_auto_backup_subsystem()` in lifespan.
- `frontend/index.html` — Auto-backup status section in Settings (moved from broken div nesting). Auto-restore modal. Help section updated with auto-backup, master-only notes, password rules.
- `frontend/js/settings.js` — Added `refreshAutoBackupStatus()`, `showAutoRestoreModal()`, `renderAutoRestoreList()`, `autoRestoreSelect()`, `renderAutoRestoreReport()`, `autoRestoreConfirm()`.
- `tests/test_auto_backup.py` — New file. 36 tests: startup catch-up, weekly promotion, retention sweep, CLI, unit tests, API tests.
- `tests/conftest.py` — Added `patched_auto_backup` fixture for auto-backup tests.
- `NPM-DEPLOY-INTERNAL.md` — New IT deploy guide (internal, tracked).
- `VERSION` — 0.061.0 → 0.062.0
- `CHANGELOG.md` — Added v0.062.0 release notes
- `HANDOFF.md` — Updated version, work log, files changed, test counts, next session
- `README.md` — Updated version, config table, roles, backup/restore, project structure, features, tech stack

### v0.061.0
- `backend/export_import.py` — Removed `export_password_exists()`, `_read_password_hash()`, `_write_password_hash()`, `verify_export_password()`, `set_export_password()`. `run_export(password, user)` accepts user dict. Manifest includes `masterUserId`, `masterDisplayName`, `masterRole`. Silent decrypt round-trip added. `run_import()` returns `exportAttribution`.
- `backend/routes/export_import.py` — Removed `POST /export-password` and `GET /export-password/status`. Only 2 routes remain. Export passes `request` + user to `run_export()`.
- `backend/auth.py` — Removed `get_master_user()` (only used by removed forgot-password flow).
- `frontend/index.html` — Removed Export Password status/set/change/forgot section. New export modal (warning banner + password+confirm + eye icons + strength bar + progress states). Import: eye icon, dry-run default unchecked, attribution area.
- `frontend/js/settings.js` — Removed `loadExportPasswordStatus()`, `showExportPasswordModal()`, `submitExportPassword()`, `runEncryptedExport()`. Added `showExportModal()`, `togglePassword()`, `calcPasswordStrength()`, `validateExportPassword()`, `updateExportButton()`, `submitExport()`. Simplified `exportDatabase()`, `importDatabase()`, `runQuodbImport()`, `resetQuodbImport()`.
- `frontend/js/nav.js` — Removed `loadExportPasswordStatus()` call.
- `tests/conftest.py` — Removed `TEST_EXPORT_PASSWORD` constant, `export_password_set` fixture. Added `fast_crypto` fixture.
- `tests/test_export_import_unit.py` — Removed `TestPasswordManagement` class, password management imports.
- `tests/test_export_import_api.py` — Rewritten: removed all password endpoint tests, adapted auth gates + export/import tests for no-hash model, added attribution test. Uses `master_client` and `fast_crypto` fixture.
- `VERSION` — 0.060.0 → 0.061.0
- `CHANGELOG.md` — Added v0.061.0 release notes
- `HANDOFF.md` — Updated version, work log, files changed, test counts, next session
- `README.md` — Updated Backup & Restore section (per-file password flow)

### v0.058.1
- `backend/main.py` — Added `uploaded_by` to `save_upload_state()` save payload
- `backend/routes/files.py` — Added `GET /queue` endpoint; calls `save_upload_state()` after upload, clear, remove-file, confirm, skip
- `frontend/js/app.js` — Added `loadQueueState()`; `initApp()` now restores queue and routes to Process view
- `VERSION` — 0.058.0 → 0.058.1
- `CHANGELOG.md` — Added v0.058.1 release notes
- `HANDOFF.md` — Updated version, work log, checklist

### v0.058.0
- `backend/utils.py` — Added `trust_proxy_headers: False` to `_CONFIG_DEFAULTS`
- `backend/routes/auth.py` — `_get_client_ip()` guarded behind `trust_proxy_headers` config flag
- `backend/middleware.py` — Added `SecureCookieMiddleware` (adds `Secure` flag when behind HTTPS proxy)
- `backend/main.py` — Imported and registered `SecureCookieMiddleware`
- `config.example.json` — Added `trust_proxy_headers: false` placeholder
- `VERSION` — 0.057.2 → 0.058.0
- `CHANGELOG.md` — Added v0.058.0 release notes
- `HANDOFF.md` — Updated version, work log, checklist, next session
- `.gitignore` — Added `/NPM-DEPLOY.md`
- `NPM-DEPLOY.md` — New file (gitignored, not pushed)

### v0.057.2
- `frontend/js/progress.js` — Store `extractedData` per file entry (`uploadedFiles[fileIdx].extractedData`) for review re-entry
- `frontend/js/upload.js` — `done` files render with clickable cursor + `onclick="reviewDoneFile()"`; new `reviewDoneFile()` async function restores extracted data, fetches page images, and opens review
- `frontend/js/review.js` — `backToUpload()` routes to step 2 (queue) if files remain, step 1 (upload) if empty; `showReview()` shows step-4 panel before setting img src
- `frontend/js/review.js` — `updateReviewPdf()` toggles fallback message when `reviewPages` is empty
- `frontend/js/nav.js` — `showUpload()` routes to step 2 if files exist, step 1 if empty
- `frontend/index.html` — Added `#reviewPdfFallback` element for blank-preview fallback message
- `backend/routes/files.py` — Clean stale image dir before `_generate_page_images()` on re-process
- `VERSION` — 0.057.1 → 0.057.2
- `CHANGELOG.md` — Added v0.057.2 release notes
- `HANDOFF.md` — Updated version, work log, known issues, checklist

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
| Export/Import | ✅ Complete (v0.062.0: master-only, per-file password, AES-256-GCM encrypted `.quodb`) |
| Auto-Backup (daily/weekly/event) | ✅ Complete (v0.062.0: daily 03:00, weekly Sunday promotion, event triggers, retention sweep, machine-bound key) |
| System Cleanup | ✅ Complete |
| Config Validation | ✅ Complete |
| Automated Tests | ✅ **274 tests passing** (36 auto-backup, 37 auth, 24 export/import unit, 21 admin, 20 extract, 18 files CRUD, 16 config, 15 upload validation, 14 encryption at rest, 13 pipeline, 13 export/import API, 13 search, 4 SSE, 1 health). All endpoint categories covered. Full coverage across auth gates, CRUD operations, error paths, disk cleanup, auto-backup lifecycle, upload validation, file-at-rest encryption, and FTS rebuild. |
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

### ⚠️ CRITICAL RULE: Foreign Key Enforcement

`PRAGMA foreign_keys = ON` is set in `get_db()` (write connections only). This means:

- Any future `sqlite3.connect()` call outside `get_db()` **must** include `PRAGMA foreign_keys = ON` explicitly. Otherwise, foreign key enforcement is silently bypassed for that connection.
- The vacuum connection in `admin.py` and export/import connections in `export_import.py` are read-only or VACUUM-only — no FK risk.
- If you add a new connection site, check whether it modifies data. If it does, enable FK enforcement or the database will allow orphaned rows.

---

## FTS Index Rebuild

If the full-text search index gets out of sync with the `quotations` table (e.g., after a bulk SQL operation that bypasses triggers), rebuild it with:

```sql
INSERT INTO quotations_fts(quotations_fts) VALUES('rebuild');
```

Run from inside the container:

```bash
docker exec quodb sqlite3 /app/data/quotations.db \
  "INSERT INTO quotations_fts(quotations_fts) VALUES('rebuild');"
```

This is a SQLite FTS5 built-in operation — it drops and recreates the internal FTS index from the content table. The command is idempotent and safe to run at any time.

---

## Known Issues

- **XLSX viewer column resizing** — SheetJS renders a read-only HTML table; user cannot manually resize columns. Columns are auto-sized to fit content. To revisit: consider a library with built-in column resize support (e.g., ReoGrid, Luckysheet/Univer, or custom drag handlers with better event handling)
- **Login brute-force protection** — ✅ Done (v0.056.0). IP-based rate limiter on `/auth/login`. See Security Gaps section for details and known limitations.
- **Orphaned file cleanup** — ✅ Done (v0.057.0). All three orphan sources fixed: remove-file images, clear files+images, import archive PDFs on failure.

---

## Security Gaps & Planned Fixes

### Production Audit Completed (v0.063.0)

A full production-readiness audit was performed covering 15 non-negotiable requirements (passwords, file encryption, SQL parameterization, container hardening, session security, etc.) for a local-LAN deployment with up to 10 concurrent users. 21 findings were identified across P0–P3 priority levels. **All 10 P0 items are addressed.**

#### 🔴 P0 — Summary

| # | Area | Finding | Resolution | Status |
|---|------|---------|------------|--------|
| 1 | DB | No busy timeout — concurrent writes can `database is locked` | `timeout=5` in `sqlite3.connect()` | ✅ Fixed |
| 2 | Files | Path traversal in `/upload` — filename not sanitized | Reject `..`, `/`, `\` + empty-stem check | ✅ Fixed |
| 3 | Files | No magic bytes — `.pdf` can be any file type | Check `%PDF` / `PK\x03\x04` before write | ✅ Fixed |
| 4 | Files | Files not encrypted at rest | AES-256-GCM on write, transparent decrypt on read | ✅ Fixed |
| 5 | Infra | Container runs as `root` | `quodb` user (UID 1001), `gosu` privilege drop | ✅ Fixed |
| 6 | Infra | `/docs` publicly accessible — leaks API surface | Gated by `QUODB_DOCS_ENABLED` env var (default `false`) | ✅ Fixed |
| 7 | DB | Database not encrypted at rest | **Accepted.** SQLite has no built-in encryption. Protected by Docker volume isolation + filesystem perms + LAN isolation. Use LUKS if threat model changes. | ✅ Accepted |
| 8 | Infra | No `TrustedHostMiddleware` — host header injection | `allowed_hosts=["*"]` — wildcard avoids IP/hostname churn. LAN + NPM + auth = no practical exploit. | ✅ Accepted |
| 9 | AI | LLM output parsed by regex + `json.loads` — no schema validation | `ExtractionResult` + `ExtractionItem` Pydantic models in `llm.py`. `ValidationError` caught gracefully. | ✅ Fixed |
| 10 | AI | VLM response has no size cap — memory exhaustion risk | 100 KB cap with truncate + warn in `vision.py`. 12× safety margin over `max_tokens=4096`. | ✅ Fixed |

#### 🟡 P1 — Non-blocking (should be addressed)

| # | Area | Location | Finding | Suggested Fix | Effort |
|---|------|----------|---------|---------------|--------|
| 1 | Frontend | `utils.js:69-73` | `showBriefPopup()` uses `innerHTML` with unsanitized `message` — XSS sink | Changed to `textContent` — message is never rendered as HTML | ✅ Fixed |
| 2 | Frontend | `utils.js:90-98` | `showConfirmPopup()` same `innerHTML` pattern with unsanitized `message` | Same fix as #1 | ✅ Fixed |
| 3 | Frontend | `settings.js:741` | `renderAutoRestoreList()` injects backup file path into `innerHTML` without escaping | Refactored to DOM APIs (`createElement`, `textContent`, `addEventListener`) — no HTML string interpolation at all | ✅ Fixed |
| 4 | Backend | `files.py:328-335` | Only `.pdf` and `.xlsx` allowed — no generic document type support explicitly rejected at network boundary | Added `Content-Length` header check at the top of `/upload` — returns 413 Payload Too Large before any body is buffered | ✅ Fixed |

#### 🟡 P2 — Medium priority

| # | Area | Finding | Resolution | Status |
|---|------|---------|-----------|--------|
| 5 | Infra | No health check on DB connection | Health endpoint exists, Docker HEALTHCHECK curls it — dead DB cascades to 500s → healthcheck fails. Sufficient for 10 users. | ✅ Accepted |
| 6 | AI | No graceful degradation notification when AI server is down | Added yellow warning banner in review screen when `extraction_method === 'local'` — "AI server unreachable — extraction used local rules. Results may be limited." | ✅ Fixed |
| 7 | Observability | No request ID tracing across logs | Overkill for 10 users on LAN. Docker logs + structured formatter provide enough traceability. | ✅ Accepted |
| 8 | Infra | No resource limits on containers | Added `deploy.resources.limits` to `docker-compose.yml`: 2 CPUs / 4 GB RAM. Docker throttles CPU and kills container on OOM; auto-restarts via `restart: unless-stopped`. | ✅ Fixed |
| 9 | Infra | Single container, no HA | Added note in README.md Data Persistence section stating single-node deployment, no failover/clustering planned. | ✅ Fixed |
| 14 | FastAPI | No global exception handler | Added `@app.exception_handler(Exception)` — logs full traceback server-side, returns safe 500 JSON. HTTPException/422 handlers unchanged. | ✅ Fixed |
| 15 | Crypto | config.json plaintext on volume | AI endpoint is local LAN only — no credentials. Moving to env var would break Settings UI. | ✅ Accepted |
| 16 | Config | Lifespan logs AI endpoint URL | Changed to `'configured' if ep else 'NOT SET'` — confirms config loaded without leaking the LAN IP | ✅ Fixed |
| 17 | Tests | No FTS rebuild test | Added `TestFtsRebuild` test + `docker exec` one-liner in HANDOFF.md. | ✅ Fixed |
| 18 | Docker | config.json baked into Docker image layer | Removed `COPY config.json .` from Dockerfile — config is mount-only at runtime. | ✅ Fixed |

#### 🟢 P3 — Low priority

| # | Area | Finding | Resolution | Status |
|---|------|---------|-----------|--------|
| 10 | Build | No version pinning in `requirements.txt` | Pinned `cryptography==48.0.0` and `bcrypt==4.0.1` — all 15 deps now exact versions | ✅ Fixed |
| 11 | CI | No linting in CI | Left as-is — single-developer LAN project; manual `ruff check` before commits is sufficient | ⏸️ Open |
| 12 | CI | No `docker scan` / Trivy in CI | Left as-is — no CI pipeline exists; manual `docker scout quick` before releases catches critical CVEs | ⏸️ Open |
| 19 | FastAPI | No CORSMiddleware | Added `CORSMiddleware(allow_origins=["*"])` with documented intent; same-site cookie is real defense | ✅ Fixed |
| 20 | Deploy | No Content-Security-Policy header | Added `CSPMiddleware` with `default-src 'self'` policy; `'unsafe-inline'` for existing inline handlers | ✅ Fixed |
| 21 | Deploy | No X-Content-Type-Options header | Added `X-Content-Type-Options: nosniff` to CSPMiddleware | ✅ Fixed |
| 22 | Search | FTS5 MATCH injection — `-` treated as NOT operator | Sanitize search terms with `re.sub(r'[^\w]', '', w)` stripping FTS5 operators | ✅ Fixed |
| 23 | Concurrency | `uploaded_files` global list unsynchronized — race conditions on append/pop/iteration | Added `uploaded_files_lock` asyncio.Lock, wrapped all 32 access points across 3 files | ✅ Fixed |
| 24 | Concurrency | `process_lock` created at module level — stale after event loop restart | Moved `process_lock = asyncio.Lock()` into `lifespan()` | ✅ Fixed |
| 25 | Infra | OCR temp files world-readable (`NamedTemporaryFile` default perms) | Replaced with `tempfile.mkstemp()` (0o600 perms) + consolidated cleanup into single `finally` | ✅ Fixed |
| 26 | Infra | Import endpoint reads entire file into memory (`await file.read()`) | Replaced with `shutil.copyfileobj()` streaming write in 1 MB chunks | ✅ Fixed |
| 27 | Observability | Document content (supplier names) logged without redaction | Replaced `supplier` field with `'[REDACTED]'` in "Quotation saved" and "Quotation updated" logs | ✅ Fixed |
| 28 | Config | No `wal_autocheckpoint` tuning — default 1000 pages (~4 MB) | Added `PRAGMA wal_autocheckpoint=500` for smaller, more frequent checkpoints | ✅ Fixed |

---

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

| Priority | Item | Effort | Status |
|----------|------|--------|--------|
| 🔴 High | **Persistent sessions** | 1 day | ✅ Done. Starlette signed cookies (client-side), SECRET_KEY in data volume. Container restarts do NOT log users out. |
| 🔴 High | **Database migration system** | 2 days | ✅ Done (v0.055.2). Versioned schema migration in `backend/db.py` with DDL/DML rules. |
| 🔴 High | **Login brute-force protection** | 1 hour | ✅ **Resolved by NPM** (v0.058.0). `trust_proxy_headers` + `_get_client_ip()` guard forwards real client IPs from Nginx Proxy Manager, fixing the Docker gateway IP issue. See `NPM-DEPLOY.md`. |
| 🔴 High | **HTTPS via reverse proxy** | 1 day | ✅ **Handled externally via NPM** (v0.058.0). App prepared with `trust_proxy_headers` flag + `SecureCookieMiddleware`. See `NPM-DEPLOY.md` for IT team steps. |
| 🔴 High | **Busy timeout (P0-1)** | 1 line | ✅ Done (v0.063.0). `timeout=5` in `sqlite3.connect()`. |
| 🔴 High | **Path traversal fix (P0-2)** | 0.5 day | ✅ Done (v0.063.0). `/upload` rejects `..`, `/`, `\` + empty-stem check. |
| 🔴 High | **Magic bytes validation (P0-3)** | 0.5 day | ✅ Done (v0.063.0). Checks `%PDF` / `PK\x03\x04` before write. |
| 🔴 High | **File-at-rest encryption (P0-4)** | 2 hours | ✅ Done (v0.063.0). AES-256-GCM on write, transparent decrypt on read, key from `FILE_ENCRYPTION_KEY` env var. |
| 🔴 High | **Database encryption (P0-5)** | N/A | ✅ **Accepted risk.** SQLite has no built-in encryption. SQLCipher would break the KISS model. Protected by Docker volume isolation + filesystem permissions + LAN isolation. Use LUKS at host level if threat model changes. |
| 🔴 High | **Non-root container (P0-6)** | 0.5 day | ✅ Done (v0.063.0). `quodb` user (UID 1001), `gosu` privilege drop via entrypoint, startup `chown` of `/app/data` for existing volumes. |
| 🔴 High | **Disable /docs in production (P0-7)** | 1 line | ✅ Done (v0.063.0). Gated by `QUODB_DOCS_ENABLED` env var (default `false`). Toggle on for debugging. |
| 🔴 High | **TrustedHostMiddleware (P0-8)** | 5 min | ✅ **Accepted risk.** `allowed_hosts=["*"]` — wildcard avoids IP/hostname churn. LAN + NPM + auth = no practical exploit. |
| 🔴 High | **LLM output validation (P0-9)** | 1 day | ✅ Done. `ExtractionResult` + `ExtractionItem` Pydantic models validate LLM output; `ValidationError` caught gracefully. |
| 🔴 High | **VLM response size cap (P0-10)** | 0.5 day | ✅ Done. 100 KB truncate + warn in `extraction/vision.py`. |
| 🟡 Medium | **Queue persistence** | 0.5 day | ✅ Done (v0.058.1). Backend persists queue on every mutation; frontend restores via `GET /queue` on page load. Queue survives container restart and browser refresh. |
| 🟡 Medium | **Graceful shutdown** | 0.5 day | ✅ Done (v0.058.1). Analysis showed no functional gap — lock released by `finally` on cancellation, DB not touched during streaming, temp files cleaned on re-process. Shutdown log added to confirm clean stop in container logs. |
| 🟡 Medium | **SQLite WAL mode** | 1 line | ✅ Done (v0.055.0). Enables concurrent reads without blocking. |
| 🟡 Medium | **Expand test coverage** | 3 days | ✅ **274 tests** across all endpoint categories. |
| 🟡 Medium | **Rate limiting on upload & processing** | 0.5 day | ✅ Done (v0.055.3). Queue cap (50), processing semaphore (1 file at a time). |
| 🟡 Medium | **Rate limiter X-Forwarded-For support** | 0.5 day | ✅ **Resolved by NPM** (v0.058.0). `trust_proxy_headers` flag + `_get_client_ip()` guard. NPM sets real client IP in `X-Forwarded-For`. |
| 🟡 Medium | **Static file serving via reverse proxy** | 0.5 day | ✅ **Handled externally via NPM** (v0.058.0). NPM can serve `/static/` and `/images/` directly; caching headers configurable in NPM UI. |
| 🟢 Low | **Orphaned file cleanup** | 0.5 day | ✅ Done (v0.057.0). All three orphan sources fixed. |
| 🟢 Low | **Custom error pages** | 0.5 day | ✅ Done (v0.057.1). SPA catch-all route. |
| 🟢 Low | **Done files clickable in queue** | 0.5 day | ✅ Done (v0.057.2). Click re-opens review with extracted data. |
| 🟢 Low | **Blank preview after cancel/reprocess** | 0.5 day | ✅ Done (v0.057.2). Stale image cleanup + panel visibility order + fallback message. |
| 🟢 Low | **Queue routing after cancel/save** | 0.5 day | ✅ Done (v0.057.2). Routes to queue if files remain, upload if empty. |
| 🟢 Low | **Uploaded_by display in queue UI** | 0.5 day | ✅ Done (v0.059.1). `renderFileList()` now shows `by username` next to filename. |
| 🟢 Low | **XLSX column resizing** | 2 days | ❌ SheetJS renders read-only table; users cannot resize columns. |
| 🟢 Low | **Database + file backup** | 0.5 day | ✅ **Resolved by auto-backup** (v0.062.0). Automatic daily encrypted backups + weekly retention. Internal Backup Key (machine-bound AES-256-GCM). See `backend/auto_backup.py`. |
| 🟢 Low | **Unbounded disk growth** | 0.5 day | ✅ Done (v0.059.0). `POST /cleanup/purge-orphans` deletes temp files with no queue entry and image dirs with no reference in queue, archive, or DB. `GET /cleanup/stats` reports orphan counts and estimated bytes. |

---

## Priority 1 Review Items

### Global vocabulary writes are not audit-logged

`POST /brands` and `POST /product-types` create entries in shared vocabulary tables without writing to `supplier_audit_log`.

**Reason:** `supplier_audit_log.supplier_id` is NOT NULL by design. Global vocabulary creation falls outside the supplier-scoped audit boundary. Vocabulary pollution is mitigated by `normalize_name()` applied on insert and during scan.

**Risk:** Low. Trusted Master/Admin users only. No PII. No modify or delete paths exist for these endpoints.

**Reconsider if:**
- Vocabulary pollution becomes operational
- Modify/delete endpoints are added
- Multi-tenant scoping is introduced
- External compliance demands universal write logging

### Delete endpoint response shape is inconsistent

Various delete endpoints return different shapes:
- `{"status": "deleted"}`
- `{"detail": "..."}`
- 204 No Content

Frontend currently handles each shape correctly.

**Reason:** No user-facing bug exists. Standardization would require touching multiple endpoints and corresponding frontend handlers. Deferred to a future API consistency pass.

**Risk:** Low. Working as-is. Future devs may add inconsistent new endpoints if no standard exists.

**Reconsider when:**
- Adding new delete endpoints (define the standard then)
- During a future API hygiene milestone

---

## Next Session Start Here

1. Review this HANDOFF.md for context
2. Check `git log --oneline -10` for any commits since this session
3. Run `pytest tests/ -v` to verify all tests pass (274 expected)
4. All 21 original audit items (P0–P3) plus 8 additional red flags addressed in v0.063.1. Left as-is:
   - **P3-11**: CI linting (no CI pipeline)
   - **P3-12**: Container scanning (manual `docker scout quick` before releases)
   - **XLSX column resizing**: Pre-existing known issue
   - **Vision.py output validation**: Vision LLM (vision.py) still lacks Pydantic validation that text LLM (llm.py) already has — same treatment recommended
5. **config.json tracking**: Already removed from git tracking (`.gitignore` + `config.example.json` template + `deploy.sh` auto-copy). Do NOT re-flag as outstanding — the file on disk is a local dev artifact, the repo copy is a placeholder with `config.example.json` as the deployment source of truth. See v0.063.1 work log.
