# CHANGELOG.md — QuoteHub Release Notes

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
