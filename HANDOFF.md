# HANDOFF.md — Session Bridge

**Purpose:** Session-to-session continuity for the coding agent.
**Authoritative for:** current state, locked decisions, open work, standards.
**NOT a changelog** — see CHANGELOG.md for release history.
**NOT an audit record** — see AUDIT.md for the closed v0.063.x production audit.

---

## 1. Session State

| Field          | Value                                                 |
|----------------|-------------------------------------------------------|
| Dev base       | v0.063.2                                              |
| Active branch  | feature/suppliers-db                                  |
| Last commit    | f1d6f12 — inline field validation                     |
| Tree state     | clean                                                 |
| Merged to dev  | NO                                                    |
| Agent state    | idle                                                  |
| Next goal      | Close P2 quick-wins (#13, #10, #8), then merge to dev |

---

## 2. Active Work — feature/suppliers-db (pending merge)

Suppliers DB module, in flight on feature branch. Highlights:
- Raw / display / canonical name model
- Stopword filtering on alias suggestions
- Phone + email render-time validation, persistent field errors
- Custom modal for dirty-guard nav (replaces native confirm)
- App-wide button color standard rollout
- Dormant code annotated (capabilities, product-types)

**Pending before merge to dev** (P2 quick-wins agreed):
- [ ] #13 — Document supplier_aliases global UNIQUE decision
- [ ] #10 — Migration v5: partial unique index on is_default_rfq_contact
- [ ] #8  — Merge confirm modal + actor in audit log
- [ ] Full pytest pass
- [ ] CHANGELOG v0.064.0 entry
- [ ] Tag + merge

**Deferred with rationale** (do NOT re-raise without new data):
- #7 Rate limiting on scan/alias — LAN trusted users
- #9 Scan query profiling — no production data yet
- #11 status='review' cleanup UI — no backlog observed
- #12 suppliers.js refactor — module too fresh, regression risk
- #14 Concurrency stress test — 1 worker + SQLite, low surface

---

## 3. Architectural Decisions (Locked)

**Deployment**
- Local-network-only app. No public exposure planned.
- Docker single-node. No HA, no clustering. SQLite cannot cluster.
- HTTPS + real client IP handled externally by NPM reverse proxy.

**Database**
- SQLite + FTS5. No swap planned.
- Database at rest NOT encrypted — accepted risk. Use LUKS at host if threat model changes.
- WAL mode on. wal_autocheckpoint=500 pages.
- Versioned migrations in backend/db.py.

**Migration Rules (MANDATORY)**
1. DDL and DML in separate migration functions. DDL auto-commits; mixing risks corruption on retry.
2. DML must be idempotent (INSERT OR IGNORE, WHERE NOT EXISTS).

**Foreign Keys**
- PRAGMA foreign_keys = ON set in get_db() only.
- Any new sqlite3.connect() outside get_db() that writes data MUST include this pragma explicitly.

**Suppliers**
- POST /suppliers accepts a single `name` field. Backend derives three storage columns:
  - `raw_name` — strip only (preserves special chars like parentheses)
  - `display_name` — whitespace collapsed
  - `canonical_name` — normalized via `normalize_name()` (lowercase, punctuation stripped except `-` and `'`)
- Aliases mirror this: `raw_alias` (original input) + `alias` (normalized).
- `supplier_aliases.alias` is globally UNIQUE — same alias cannot exist for two suppliers.
- Scan matches quotation `supplier` text against `raw_name` + `raw_alias` (case-insensitive exact match, not fuzzy).
- Dormant capability/product-type endpoints live in `backend/routes/suppliers.py`, registered but UI-removed; annotated DORMANT. Reactivate at RFQ milestone.

---

## 4. Open Bugs & Enhancements

**Bugs**
- Immediate bugs addressed. Field testing pending — re-audit after real use.

**Enhancements**
- Verify auto-backup round-trip includes new suppliers tables (supplier_brands, supplier_aliases, supplier_audit_log, etc.) and survives restore. Not tested end-to-end since suppliers milestone.

**Deferred with rationale** — see §2.

---

## 5. Standards

### Button Colors

| Class          | Color | When to use                                                       |
|----------------|-------|-------------------------------------------------------------------|
| .btn-primary   | Blue  | Single primary commit on screen: Save, Submit, Confirm, New       |
| .btn-secondary | Gray  | Everything else: Add (inline), Cancel, Back, Reorder, Scan, Merge |
| .btn-warning   | Amber | Cautionary but recoverable: Inactivate, Override                  |
| .btn-danger    | Red   | Destructive: Purge, Delete, Remove                                |

Rules: only one .btn-primary visible per screen. .btn-success retired.

### Success Feedback

Use `showBriefPopup(message)` only on real persistence events (Save, Create, Inactivate, Merge, Delete, Upload complete).
NOT for inline form additions, visual state, read-only ops, or operations with explicit modal confirmation.
Message: 1–4 words, past tense, no end punctuation. e.g. "Supplier saved".

### FTS Index Rebuild

If quotations_fts drifts from quotations:


docker exec quodb sqlite3 /app/data/quotations.db 
"INSERT INTO quotations_fts(quotations_fts) VALUES('rebuild');"

Idempotent. Safe at any time.

---

## 6. Dormant Features

Preserved but not UI-exposed. Backend + tests intact.

**Capabilities** — `/capabilities` CRUD endpoints, CapabilityCreate/Update models, helpers, TestCapabilitiesCRUD, CSS scaffolding.
**Product Types** — `/product-types` GET/POST endpoints, ProductTypeCreate model.

Reactivate when: RFQ milestone introduces "supplier covers brand X type Y" matching. Backend is ready; UI only.

---

## 7. Agent Onboarding

**Read order each session:**
1. This file (HANDOFF.md) — sections 1–4 are mandatory.
2. AGENTS.md — workflow + rules.
3. SPEC.md — feature scope.
4. Sections 5–6 of this file — reference only, do not re-litigate.

**When summarizing for context handoff, preserve verbatim:**
- Section 1 (Session State)
- Section 3 (Architectural Decisions)
- Section 4 (Open Bugs)

**Do not re-flag:**
- Items listed under "Deferred with rationale" (§2)
- Accepted risks under Architectural Decisions (§3)
- Items already shipped in CHANGELOG.md
- Items closed in AUDIT.md

---

## 8. Related Documents

| File         | Role                                          |
|--------------|-----------------------------------------------|
| AGENTS.md    | Agent workflow + rules                        |
| SPEC.md      | Feature scope + acceptance criteria           |
| CHANGELOG.md | Release history                               |
| AUDIT.md     | Closed v0.063.x production audit              |
| README.md    | Project overview + deployment                 |

Do not add release notes or audit findings to this file.
