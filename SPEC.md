# SPEC.md — QuoteHub Feature Specification

## Project

QuoteHub — AI-powered quotation document processing system.

## Goal

Upload PDF and XLSX quotations, extract structured data using AI, search across all processed documents, and manage quotations with role-based access.

---

## Core Features

### 1. Upload & Process
- Upload PDF and XLSX quotation files via drag-drop or file picker
- Queue multiple files for batch processing
- Extract data using LLM (primary) or local rules (fallback)
- Multi-page PDF support with streaming progress feedback

### 2. Review & Edit
- Review extracted data in editable table format
- Inline editing of Brand, Model, Description, Currency, Unit Price, Supplier, Date
- Find & Replace for bulk edits
- PDF viewer with zoom and page navigation

### 3. Search
- Full-text search across suppliers, items, descriptions
- Sortable columns (Brand, Model, Description, Currency, Unit Price, Date, Supplier)
- Search results with PDF preview

### 4. Settings
- AI server connection configuration
- Extraction ON/OFF toggle (auto-mode detects scanned vs text PDFs)
- Export encrypted backup (AES-256-GCM `.quodb` with per-file password)
- Import encrypted backup (`.quodb` with dry-run mode)
- Automatic daily backups at 03:00 with weekly retention (master-only restore)
- System cleanup with document type filter
- User management (master only)

### 5. Authentication & Roles
- Three roles: User, Admin, Master
- User: search and browse only
- Admin: upload, process, edit, export, view logs (no settings changes)
- Master: full access including all settings, import, cleanup, user management

---

## Edge Cases

- Empty file upload must be rejected
- Duplicate filename detection warns before processing
- AI server unavailable shows clear error, allows retry
- Malformed PDF with no extractable data shows warning
- Concurrent uploads handled safely
- Session timeout after 15 minutes idle
- Database locked during concurrent writes handled gracefully

---

## Constraints

- **Backend:** Python, FastAPI, SQLite with FTS5
- **Frontend:** Vanilla HTML/CSS/JS (no frameworks, no bundlers)
- **AI:** External LLM only (LM Studio / OpenAI-compatible API)
- **Deployment:** Docker only, data in named volume
- **Auth:** Custom 3-role system (no external providers)
- **Storage:** SQLite for data, filesystem for PDFs, XLSX files, and images

---

## Acceptance Criteria

- User can upload, process, review, edit, and save quotations
- Search returns relevant results with sortable columns
- AI extraction works with external LLM server
- Role-based access controls UI visibility correctly
- Export/Import backup/restore works reliably
- System cleanup filters by document type and age
- All features work in Docker deployment
- No secrets committed to repository
