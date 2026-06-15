# HANDOFF.md — Session Bridge

## Current Version

**v0.053.2** (dev branch)

---

## Last Completed Work

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

- `backend/extraction/vision.py` — Vision LLM with single-prompt approach for all pages, fixed 200 DPI, no post-processing
- `backend/extraction/llm.py` — Text LLM with simplified ~15 line prompt, no field mapping/few-shot
- `backend/extraction/router.py` — Single auto mode router; auto-detects scanned vs text vs XLSX
- `backend/extraction/__init__.py` — Updated exports
- `backend/routes/admin.py` — Removed `extraction_mode`/`llm_dpi` validation; added `extraction_enabled` boolean check
- `backend/routes/files.py` — Added `pdf_path` to parse result for Vision LLM; removed `extraction_mode` param
- `backend/utils.py` — Added `normalize_date()`, removed `extraction_mode` defaults
- `frontend/index.html` — 6-mode dropdown → ON/OFF AI toggle; OCR settings preserved
- `frontend/js/settings.js` — Removed mode/DPI from save; added `extraction_enabled` checkbox
- `frontend/js/nav.js` — Removed extraction mode badge function
- `tests/test_config_validation.py` — Replaced mode/DPI tests with `extraction_enabled` tests
- `tests/conftest.py` — Updated fixture config

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
| Automated Tests | ✅ 41 tests passing |
| Vision LLM (scanned PDFs) | ✅ Working (fixed pdf_path bug) |
| Multi-page PDF extraction | ✅ Working (single prompt for all pages) |

---

## Known Issues

- `data/images/` directory may have orphaned files (permission issues with Docker-owned files)
- **XLSX viewer column resizing** — SheetJS renders a read-only HTML table; user cannot manually resize columns. Columns are auto-sized to fit content. To revisit: consider a library with built-in column resize support (e.g., ReoGrid, Luckysheet/Univer, or custom drag handlers with better event handling)

---

## Extraction Pipeline Reference

- **Scanned PDF** (avg text chars < 50/page) → Vision LLM (page-by-page image analysis)
- **Text PDF** (avg text chars >= 50/page) → Text LLM (extracted page text)
- **XLSX** → Text LLM (openpyxl text)
- **Any fail** → Local rules fallback
- **AI disabled** (`extraction_enabled: false`) → Local rules only

---

## Next Session Start Here

1. Review this HANDOFF.md for context
2. Check `git log --oneline -10` for any commits since this session
3. Run `pytest tests/ -v` to verify all tests pass
4. Ask user what they want to work on next
