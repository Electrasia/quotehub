# HANDOFF.md — Session Bridge

## Current Version

**v0.051.0** (dev branch)

---

## Last Completed Work

### v0.051.0 — Bug Fixes + Deploy Improvement
- Fix: upload error banner persists after Clear All
- Fix: must_change_password flag not cleared after password change (backend + frontend)
- Fix: users table missing on fresh Docker installs (init_db now creates it)
- Feature: deploy.sh shows initial master password after fresh install

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

---

## Files Changed Recently

- `backend/db.py` — users table creation in init_db(), migration function for future columns
- `backend/routes/auth.py` — clear_must_change_password after password change
- `frontend/js/auth.js` — acknowledge init password after first change
- `frontend/js/upload.js` — clear error banner on Clear All
- `deploy.sh` — show initial master password after fresh install

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
