# AGENTS.md — QuoteHub Development Rules

## Default Mode: REVIEW ONLY

Do not edit files unless user explicitly approves a patch plan.

**Workflow:** Inspect → Report → Propose → Approve → Apply → Verify → Summarize

**Rules:**
- Do not assume intended behavior
- Do not broaden scope
- Do not make unrelated cleanup
- On errors: Report → Propose fix → Wait → Fix

---

## User Interaction

- Explain technical things in plain language
- Confirm before destructive actions (delete, drop, force-push)
- If approval is unclear, ask before editing

---

## Git Workflow

- **Default branch:** `dev`
- **Release branch:** `main` (merge only after user approval)
- **Versioning:** `VERSION` file is source of truth
- **Commits:** concise, imperative, explain why
- **Never commit secrets** (IPs, passwords, real endpoints)

---

## Code Organization

- **Backend:** `backend/` — Python, FastAPI, SQLite
- **Frontend:** `frontend/` — Vanilla HTML/CSS/JS (no frameworks)
- **Config:** `config.json` mounted from host, repo copy has placeholders only
- **No new top-level directories** without discussion

---

## UI Rules

- Top nav only: Process / Search / Settings
- Step-based Process flow: Upload → Queue → Processing → Review
- Inline table editing, modals only for confirmations
- Role-based visibility (user/admin/master) — do not break

---

## Deployment

- Docker only, data in named volume `quodb_data`
- `deploy.sh` is canonical deploy command
- `GIT_COMMIT` baked at build time

---

## Out of Scope (without explicit approval)

- New frameworks, bundlers, or build tools
- New databases (SQLite is the choice)
- Embedded AI models (must stay external)
- Cloud services, telemetry, analytics
