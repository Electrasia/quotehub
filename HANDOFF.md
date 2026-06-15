# HANDOFF.md — Session Bridge

## Current Version

**v0.053.4** (dev branch)

---

## Last Completed Work

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
| Export/Import | ✅ Complete |
| System Cleanup | ✅ Complete |
| Config Validation | ✅ Complete |
| Automated Tests | ✅ 84 tests passing |
| Vision LLM (scanned PDFs) | ✅ Working (fixed pdf_path bug) |
| Multi-page PDF extraction | ✅ Working (single prompt for all pages) |

---

## Known Issues

- `data/images/` directory may have orphaned files (permission issues with Docker-owned files)
- **XLSX viewer column resizing** — SheetJS renders a read-only HTML table; user cannot manually resize columns. Columns are auto-sized to fit content. To revisit: consider a library with built-in column resize support (e.g., ReoGrid, Luckysheet/Univer, or custom drag handlers with better event handling)

---

## Extraction Pipeline Reference

- **Scanned PDF** (avg text chars < 50/page) → Vision LLM (page-by-page image analysis, 200 DPI)
- **Text PDF** (avg text chars >= 50/page) → Text LLM (all pages combined, max_tokens: 4096)
- **XLSX** → Text LLM (each sheet processed separately, max_tokens: 8192)
- **Any fail** → Local rules fallback
- **AI disabled** (`extraction_enabled: false`) → Local rules only

---

## Next Session Start Here

1. Review this HANDOFF.md for context
2. Check `git log --oneline -10` for any commits since this session
3. Run `pytest tests/ -v` to verify all tests pass (currently 84)
4. Review the next recommended step — see user's latest recommendation
