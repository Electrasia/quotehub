# CHANGELOG.md — QuoteHub Release Notes

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
