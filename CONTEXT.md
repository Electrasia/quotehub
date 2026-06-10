# CONTEXT.md — QuoteHub Project Context

## Architecture Summary

```
Frontend (Vanilla JS) → FastAPI Backend → SQLite Database
                           ↓
                    External LLM (LM Studio)
                           ↓
                    PDF Files (archive/)
```

- **Frontend:** Single-page app served at `/static`, vanilla HTML/CSS/JS
- **Backend:** FastAPI with modular routes (auth, files, admin, ai)
- **Database:** SQLite with FTS5 for full-text search
- **AI:** External LLM server (OpenAI-compatible API), not embedded

---

## Tech Stack

| Layer | Technology | Version |
|-------|------------|---------|
| Backend | Python | 3.11 |
| Web Framework | FastAPI | Latest |
| Database | SQLite | 3.x |
| Search | FTS5 | SQLite extension |
| PDF Parsing | pdfplumber, PyMuPDF | Latest |
| OCR | Pillow, Tesseract (optional) | Latest |
| Container | Docker | Latest |
| Frontend | Vanilla JS | ES6+ |

---

## Key Dependencies

- **fastapi** — Web framework
- **uvicorn** — ASGI server
- **pydantic** — Data validation
- **pdfplumber** — PDF text/table extraction
- **PyMuPDF** — PDF parsing (alternative)
- **Pillow** — Image processing
- **requests** — HTTP client for LLM calls

---

## Domain Assumptions

1. **Single-user Docker deployment** — Each instance runs for one user/team
2. **External LLM required** — AI extraction depends on LM Studio or similar
3. **PDF is primary input** — All quotations arrive as PDF files
4. **3-role auth sufficient** — User/Admin/Master covers all access needs
5. **SQLite adequate** — No need for PostgreSQL/MySQL at this scale
6. **Files stored locally** — No cloud storage integration

---

## Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | SQLite | Simple, no setup, sufficient for single-user |
| Frontend | Vanilla JS | No build step, simple deployment |
| AI Integration | External LLM | Flexibility, no embedded models |
| Auth | Custom 3-role | Full control, no external providers |
| Deployment | Docker | Consistent environment, easy updates |

---

## Known Constraints

- Must not add new frameworks or build tools
- Must not add new databases (SQLite is the choice)
- Must not embed AI models (external only)
- Must not use cloud services or telemetry
- Must keep `config.json` secrets out of git
- Must maintain backward compatibility with existing data
