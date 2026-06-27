# AUDIT.md — Production Audit Record

> Closed historical record of the production-readiness audit performed during v0.063.0.
> Extracted verbatim from HANDOFF.md. Do not edit — this file is archival.

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
