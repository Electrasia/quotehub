# HANDOFF.md — Session Bridge

## Current Version

**v0.052.1** (dev branch)

---

## Last Completed Work

### v0.052.1 — XLSX Preview Fixes
- Fix: XLSX preview rendering — auto-sized columns, cell borders, header styling
- Fix: trim empty trailing columns in XLSX preview
- Fix: text wraps within cells instead of spilling into adjacent columns
- Fix: New Window button uses original filename (works for PDF, XLSX, etc.)
- Fix: archive endpoint serves correct MIME type per file extension
- Feature: XLSX New Window opens interactive read-only table view via SheetJS
- Feature: PDF still opens in new browser tab (unchanged)

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

- `backend/routes/admin.py` — search with document_type filter, LIMIT 10 for empty search, supplier in item filtering, archive MIME types
- `backend/routes/files.py` — reject save if items list empty, XLSX preview rendering improvements
- `frontend/index.html` — document type dropdown, signout text
- `frontend/js/app.js` — reviewOriginalFilename global variable
- `frontend/js/search.js` — document type parameter, limited results handling
- `frontend/js/review.js` — save button validation, updateDocumentTypeWarning timing fix, SheetJS XLSX viewer
- `frontend/js/xlsx.full.min.js` — SheetJS library for client-side XLSX parsing
- `frontend/style.css` — search dropdown styling

---

## Current Status vs SPEC.md

| Feature | Status |
|---------|--------|
| Upload & Process | ✅ Complete |
| Review & Edit | ✅ Complete |
| Search | ✅ Complete |
| Settings | ✅ Complete |
| Authentication & Roles | ✅ Complete |
| Export/Import | ✅ Complete |
| System Cleanup | ✅ Complete |
| Config Validation | ✅ Complete |
| Automated Tests | ✅ 41 tests passing |

---

## Known Issues

- `data/images/` directory may have orphaned files (permission issues with Docker-owned files)
- **XLSX viewer column resizing** — SheetJS renders a read-only HTML table; user cannot manually resize columns. Columns are auto-sized to fit content. To revisit: consider a library with built-in column resize support (e.g., ReoGrid, Luckysheet/Univer, or custom drag handlers with better event handling)

---

## Next Session Start Here

1. Review this HANDOFF.md for context
2. Check git log for any commits since this session
3. Run `pytest tests/ -v` to verify all tests pass
4. Ask user what they want to work on next
