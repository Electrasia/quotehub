# CHANGELOG.md — QuoteHub Release Notes

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
