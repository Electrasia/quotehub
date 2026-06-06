"""
Local PDF parser module for QuoteHub.

Phase 1 (v0.037.0): provides read-only text + table extraction via
pdfplumber and PyMuPDF. Used by the /debug/parse endpoint to let
the operator inspect what local parsers can see BEFORE wiring them
into the main processing flow.

Phase 3+ will add format_for_llm() to convert the output into a
prompt-friendly CSV-like string for the LLM normalization step.

Both parsers are wrapped to never raise — a failing parser returns
{"available": False, "error": "..."} so the endpoint can still
return results from the other parser.
"""
import time
from pathlib import Path
from typing import Any

# Per-page text cap to keep debug responses small.
# Real quotations are <5KB of text per page; 8KB is safe and shows
# the full document in almost all cases.
MAX_TEXT_CHARS_PER_PAGE = 8000

# Per-table row cap for the same reason.
MAX_TABLE_ROWS = 200

# pdfplumber table-detection strategy.
# We prefer "lines/lines" (the default) because it gives the cleanest
# table structure — cells stay whole even when they contain multi-line
# text. We fall back to "text/text" only when lines/lines fails to
# capture meaningful data (< MIN_RICH_ROWS_FOR_CLEAN_STRATEGY rich rows),
# because text/text can split multi-line cells into separate rows.
#
# Threshold rationale: a "rich row" is a row with >= 3 non-empty cells.
# - lines/lines on a clean bordered table: every data row is rich
#   (item #, brand, model, description, qty, price, total — 7 cells).
# - lines/lines on a borderless/partial table: only the header is rich
#   (the merged header cell), data rows are empty in most columns.
# We use threshold=2 so a 1-2 item page (like the last page of a
# multi-page quote) still uses the clean strategy when it works.
# The LLM normalizer in Phase 3 will use TEXT mode (which is always
# complete) anyway, so table structure is purely diagnostic.
MIN_RICH_ROWS_FOR_CLEAN_STRATEGY = 2


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"... [truncated, total {len(s)} chars]"


def _count_rich_rows(tables) -> int:
    """A 'rich row' is a row with >= 3 non-None cells. This indicates
    meaningful data per row, which is a better signal than raw cell
    count (lines/lines can pack 7 meaningful cells into one row, while
    text/text can spread them across multiple rows of 1-2 cells each)."""
    return sum(1 for t in tables for row in t if sum(1 for c in row if c) >= 3)


def _extract_best_tables(page) -> tuple[list, str, int]:
    """Pick the best table-extraction strategy for this page.

    Returns (tables, strategy_name, rich_rows).

    Strategy:
      1. Try "lines/lines" first (default; cleanest structure).
      2. If it yields >= MIN_RICH_ROWS_FOR_CLEAN_STRATEGY rich rows,
         return it.
      3. Otherwise also try "text/text" (works on borderless tables).
      4. Return whichever has more rich rows.
    """
    # Try the clean default first
    try:
        clean_tables = page.extract_tables(
            table_settings={"vertical_strategy": "lines", "horizontal_strategy": "lines"}
        ) or []
    except Exception:
        clean_tables = []
    clean_score = _count_rich_rows(clean_tables)
    if clean_score >= MIN_RICH_ROWS_FOR_CLEAN_STRATEGY:
        return clean_tables, "lines/lines", clean_score

    # Default failed — try the fallback
    try:
        text_tables = page.extract_tables(
            table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"}
        ) or []
    except Exception:
        text_tables = []
    text_score = _count_rich_rows(text_tables)
    if text_score > clean_score:
        return text_tables, "text/text", text_score
    return clean_tables, "lines/lines", clean_score


def parse_with_pdfplumber(pdf_path: str) -> dict[str, Any]:
    """Extract text + tables with pdfplumber. Never raises."""
    out: dict[str, Any] = {
        "available": True,
        "parser": "pdfplumber",
        "time_ms": 0,
        "pages": [],
        "total_text_chars": 0,
        "total_tables": 0,
        "total_table_rows": 0,
        "table_strategies_used": [],   # one per page, for transparency
    }
    try:
        import pdfplumber  # local import so a missing dep is caught here
    except ImportError as e:
        out["available"] = False
        out["error"] = f"pdfplumber not installed: {e}"
        return out

    t0 = time.time()
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                text = _truncate(text, MAX_TEXT_CHARS_PER_PAGE)
                tables, strat_name, score = _extract_best_tables(page)
                page_tables = []
                for t_idx, table in enumerate(tables, start=1):
                    rows = table[:MAX_TABLE_ROWS]
                    page_tables.append({
                        "table_index": t_idx,
                        "row_count": len(table),
                        "shown_rows": len(rows),
                        "rows": rows,
                    })
                out["pages"].append({
                    "page": page_idx,
                    "text": text,
                    "text_chars": len(text),
                    "tables": page_tables,
                })
                out["total_text_chars"] += len(text)
                out["total_tables"] += len(page_tables)
                out["total_table_rows"] += sum(t["row_count"] for t in page_tables)
                out["table_strategies_used"].append({
                    "page": page_idx,
                    "strategy": strat_name,
                    "rich_rows": score,
                })
    except Exception as e:
        out["available"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        out["time_ms"] = int((time.time() - t0) * 1000)
    return out


def parse_with_pymupdf(pdf_path: str) -> dict[str, Any]:
    """Extract text + per-page metadata with PyMuPDF. Never raises."""
    out: dict[str, Any] = {
        "available": True,
        "parser": "pymupdf",
        "time_ms": 0,
        "pages": [],
        "total_text_chars": 0,
    }
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        out["available"] = False
        out["error"] = f"pymupdf not installed: {e}"
        return out

    t0 = time.time()
    try:
        doc = fitz.open(pdf_path)
        try:
            for page_idx, page in enumerate(doc, start=1):
                text = page.get_text("text") or ""
                text = _truncate(text, MAX_TEXT_CHARS_PER_PAGE)
                # Block count is a rough layout signal
                try:
                    blocks = page.get_text("blocks")
                    block_count = len(blocks)
                except Exception:
                    block_count = None
                out["pages"].append({
                    "page": page_idx,
                    "text": text,
                    "text_chars": len(text),
                    "block_count": block_count,
                })
                out["total_text_chars"] += len(text)
        finally:
            doc.close()
    except Exception as e:
        out["available"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        out["time_ms"] = int((time.time() - t0) * 1000)
    return out


def parse_pdf(pdf_path: str) -> dict[str, Any]:
    """Run all available parsers and return a combined debug response.

    Top-level shape:
        {
          "filename": "...",
          "file_size": int,
          "num_pages": int,
          "parsers": {
            "pdfplumber": { ... },
            "pymupdf": { ... }
          }
        }
    """
    p = Path(pdf_path)
    if not p.exists():
        return {"error": f"File not found: {pdf_path}"}

    file_size = p.stat().st_size

    # Use pdfplumber to get page count quickly (it errors on non-PDFs).
    num_pages = 0
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            num_pages = len(pdf.pages)
    except Exception:
        try:
            import fitz
            doc = fitz.open(pdf_path)
            num_pages = len(doc)
            doc.close()
        except Exception as e:
            return {
                "filename": p.name,
                "file_size": file_size,
                "error": f"Could not open PDF with either parser: {e}",
            }

    return {
        "filename": p.name,
        "file_size": file_size,
        "num_pages": num_pages,
        "parsers": {
            "pdfplumber": parse_with_pdfplumber(pdf_path),
            "pymupdf": parse_with_pymupdf(pdf_path),
        },
    }


def format_for_llm(parse_result: dict) -> str:
    """Format a parse_pdf() result into a CSV-like string the LLM can
    read in Phase 3 to extract structured data.

    Phase 2 (v0.037.0): preview only. The text is shaped for an LLM
    prompt but no LLM call is made here. Phase 3 will design the
    actual normalization prompt.

    Structure of the output (one section per page):
        === Page N ===
        [text]
        <full text from pdfplumber>
        [pymupdf]
        <full text from pymupdf, for cross-checking>
        [tables]
        <pipe-delimited rows from pdfplumber, with row index>

    All pipes inside cell values are replaced with ' / ' to keep the
    CSV unambiguous. Truncated cells are marked.
    """
    import re as _re

    def _clean(s):
        if s is None:
            return ""
        s = str(s).replace("|", " / ").replace("\r", " ").replace("\n", " ⏎ ")
        return _re.sub(r"\s+", " ", s).strip()

    lines = []
    lines.append(f"[Document]")
    lines.append(f"filename: {parse_result.get('filename', '?')}")
    lines.append(f"pages: {parse_result.get('num_pages', 0)}")
    lines.append("")

    pp = parse_result.get("parsers", {}).get("pdfplumber", {})
    pm = parse_result.get("parsers", {}).get("pymupdf", {})

    for page_idx in range(parse_result.get("num_pages", 0)):
        page_no = page_idx + 1
        lines.append(f"=== Page {page_no} ===")
        # pdfplumber text
        pp_page = next((p for p in pp.get("pages", []) if p.get("page") == page_no), None)
        if pp_page:
            lines.append(f"[pdfplumber text — {pp_page.get('text_chars', 0)} chars]")
            lines.append(pp_page.get("text", "").rstrip())
        # pymupdf text
        pm_page = next((p for p in pm.get("pages", []) if p.get("page") == page_no), None)
        if pm_page:
            lines.append("")
            lines.append(f"[pymupdf text — {pm_page.get('text_chars', 0)} chars]")
            lines.append(pm_page.get("text", "").rstrip())
        # pdfplumber tables (CSV)
        if pp_page and pp_page.get("tables"):
            lines.append("")
            lines.append(f"[pdfplumber tables — {len(pp_page['tables'])} table(s)]")
            for t in pp_page["tables"]:
                lines.append(f"  -- table {t.get('table_index')}: "
                             f"{t.get('row_count')} rows x "
                             f"{len(t['rows'][0]) if t.get('rows') else 0} cols --")
                for r_idx, row in enumerate(t.get("rows", [])):
                    cells = [_clean(c) for c in row]
                    lines.append(f"  [{r_idx}] " + " | ".join(cells))
        lines.append("")

    return "\n".join(lines)
