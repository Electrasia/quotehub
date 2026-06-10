# HANDOFF.md — Session Bridge

## Current Version

**v0.050.0** (dev branch)

---

## Last Completed Work

### v0.050.0 — Hardening + Automated Tests
- Config validation: timeout (10-300), retries (1-10), extraction_mode enum, endpoint URL format, booleans
- Empty file upload rejection: backend validates extension + 0-byte, frontend checks size
- Config default consistency: files.py uses get_config_data() (was hardcoded)
- config.example.json: extraction_mode key added, stale llm_fallback_enabled removed
- Automated test suite: 41 tests across 3 files (config validation, upload validation, extraction)
- Error banner for upload failures (replaces ephemeral popups)

### v0.049.0 — Admin Role Restrictions + Documentation
- Admin role: hidden General, Extraction, Cleanup; Import disabled
- README and Help view updated with correct admin permissions
- Stale debug docstrings cleaned up

### v0.048.0 — Bug Fixes + Dev Tools Removal
- Search sort fix: Date and Supplier columns now work correctly
- Search delete fix: rowcount check, image dir cleanup, accurate count
- Cleanup fix: ImportError for DB_PATH resolved
- Developer Tools feature removed (843 lines deleted)

---

## Files Changed Recently

- `backend/routes/files.py` — Upload validation, extraction_mode default fix
- `backend/routes/admin.py` — Config validation (_validate_config), HTTP 422 on invalid
- `backend/utils.py` — Canonical _CONFIG_DEFAULTS
- `frontend/js/upload.js` — Client-side validation, error banner
- `frontend/js/settings.js` — Config save error display
- `frontend/index.html` — #uploadErrors div
- `config.example.json` — extraction_mode key
- `tests/conftest.py` — Shared fixtures
- `tests/test_config_validation.py` — 15 tests
- `tests/test_upload_validation.py` — 5 tests
- `tests/test_extract.py` — 20 tests

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

---

## Next Session Start Here

1. Review this HANDOFF.md for context
2. Check git log for any commits since this session
3. Run `pytest tests/ -v` to verify all tests pass
4. Ask user what they want to work on next
