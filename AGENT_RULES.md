# Agent Rules — QuoteHub Development

These rules apply to any AI coding assistant working on the QuoteHub codebase.
They are mandatory. When in doubt, ask before acting.

---

## 0. Default Mode: REVIEW ONLY

Do not create, edit, delete, rename, refactor, format, or patch any file
unless the user explicitly approves a specific patch plan.

**Required workflow:**
1. Inspect relevant files.
2. Report confirmed findings.
3. Propose a patch plan.
4. Ask for approval.
5. Wait.
6. Apply only approved changes.
7. Verify.
8. Summarize.

**Rules:**
- Do not assume intended behavior.
- Do not broaden scope.
- Do not make unrelated cleanup changes.
- After context compaction, return to REVIEW ONLY mode.
- If approval is unclear, ask before editing.
- Auth, database, Docker, backup/restore, deletion, and AI-processing behavior
  require specific approval.

---

## 1. Project Context

- **Name:** QuoteHub (internal: QuoDB). Always use "QuoteHub" in user-facing text and URLs.
- **Purpose:** AI-powered quotation PDF processing — upload PDFs, extract data via external VLM, search and edit results.
- **User:** Non-technical. They run the app at http://localhost:8000 in Docker. They do not read code.
- **Repo:** github.com/Electrasia/quotehub

---

## 2. User Interaction

- **Wait for explicit approval before any non-trivial change.** Never auto-fix bugs, refactor, or edit files "to clean up." Propose the fix, get confirmation, then act.
- **Explain technical things in plain language.** No jargon unless the user uses it first.
- **Confirm before destructive actions:** delete files, drop database rows, force-push, reset, rebase.
- **Confirm before cleanup of session/temp files.**
- **On errors, report → propose fix → wait for approval → fix.** Never silently retry or patch around an error.

---

## 3. Git Workflow

- **Default branch:** `dev`. Work here.
- **Release branch:** `main`. Only merge from `dev` after the user approves.
- **Releases are tagged:** annotated tag `vX.YYY.Z` on `main` after merge.
- **Versioning:** MAJOR for breaking/feature chapters, MINOR for features, PATCH for bug fixes. Bump in `VERSION` file.
- **Commit messages:** concise, imperative, with a short body explaining why.
- **Never force-push, rebase, or amend published history** without explicit approval.
- **Never commit secrets or personal data** (real AI endpoints, model names, IPs, passwords). The repo's `config.json` must contain placeholders only.

---

## 4. Code Organization

- **Backend:** Python, FastAPI, SQLite. Lives in `backend/`. All endpoints in `main.py`. Auth primitives in `auth.py`.
- **Frontend:** Vanilla HTML/CSS/JS, no frameworks. `frontend/index.html` + `frontend/style.css` + `frontend/js/*.js` (split by concern: app, auth, nav, upload, search, settings, users).
- **Config:** `config.json` is mounted from the host at runtime. The repo copy must have empty/placeholder values only.
- **Static files:** backend serves `frontend/` at `/static`. HTML references `/static/style.css` and `/static/js/*.js`.
- **No new top-level directories** without discussion. Extend existing ones.

---

## 5. UI Rules (must follow `rules.md/`)

The files in `rules.md/` (UI_SYSTEM_RULES, UI_LAYOUT_SPEC, UX_PRINCIPLES) are the source of truth for the UI. They are mandatory:
- Top nav only: Process / Search / Settings. No sidebar, no dropdowns for nav.
- Process flow is step-based: 1. Upload → 2. Queue → 3. Processing → 4. Review.
- All editing via inline tables, not modals (modals only for confirmations, settings sub-actions, and pre-existing dialogs).
- AI starts disconnected. User must click "Connect to AI Server" in the header.
- Role-based visibility: `body.role-user .ai-admin-master { display: none }` etc. — don't break role hiding.

---

## 6. Deployment

- **Docker only.** No bare-metal installation steps.
- **Data persists in named volume `quodb_data` (external).** Never change this to a bind mount or local path.
- **`config.json` mounted from host** (not baked into image).
- **`deploy.sh`** is the canonical deploy/upgrade command. It bakes `GIT_COMMIT` into the image.
- **`docker compose up -d --build`** also works but requires `GIT_COMMIT` in `.env` (gitignored) for the commit hash to appear in the app header.
- **Build arg:** `GIT_COMMIT` — set automatically by `deploy.sh` or via `.env`.

---

## 7. Versioning & Display

- **`VERSION` file** at repo root is the single source of truth for release version.
- **`GIT_COMMIT` file** is generated at build time (via Dockerfile `ARG GIT_COMMIT`). Repo copy is gitignored.
- **App header shows** `v{VERSION} ({GIT_COMMIT})`. If commit shows as `unknown`, the build was missing the `GIT_COMMIT` arg.

---

## 8. Out of Scope

Do NOT add (without explicit approval):
- New frameworks, bundlers, or build tools (vanilla JS only).
- New databases (SQLite is the choice).
- Embedded AI models (must stay external — LM Studio / OpenAI-compatible API).
- Cloud services, telemetry, analytics.
- Authentication providers (custom 3-role auth is the choice).
- Auto-update mechanisms inside the app.
