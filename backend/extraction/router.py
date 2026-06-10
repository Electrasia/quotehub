"""
backend/extraction/router.py — Extraction mode router for QuoteHub.

Handles mode selection and fallback logic between local and LLM extractors.

Extraction Modes:
  - llm_first: Try LLM first, fall back to local if LLM fails or returns 0 items
  - local_first: Try local first, fall back to LLM if local returns 0 items
  - llm_only: Only use LLM (no fallback)
  - local_only: Only use local rules (no fallback)

The router provides a unified async interface that can be called from
any endpoint (process-stream, debug/extract, etc.).
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .local import extract_items as local_extract
from .llm import normalize_pages_with_llm

logger = logging.getLogger(__name__)


# ─── Date & Currency Fallback Detection ──────────────────────

_CURRENCY_RE = re.compile(
    r"\b(HKD|USD|EUR|GBP|JPY|CNY|RMB|MOP|AUD|CAD|SGD|TWD|KRW|THB|MYR|PHP|IDR|VND|NZD|CHF|SEK|NOK|DKK|INR|PKR|BDT|LKR|NPR|MMK|KHR|LAK)\b",
    re.IGNORECASE,
)

_DATE_PATTERNS = [
    # YYYY-MM-DD
    (re.compile(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b"), "%Y-%m-%d"),
    # DD/MM/YYYY or DD-MM-YYYY
    (re.compile(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{4})\b"), "dmy"),
    # Month DD, YYYY
    (re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b", re.IGNORECASE), "mdy"),
    # DD Month YYYY
    (re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d{2})\b", re.IGNORECASE), "dmy_alt"),
]


def _detect_currency(text: str) -> str:
    """Detect currency code from text using regex."""
    m = _CURRENCY_RE.search(text)
    if m:
        return m.group(1).upper()
    return ""


def _detect_date(text: str) -> str:
    """Detect date from text and normalize to YYYY-MM-DD."""
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = m.group(1) if m.lastindex else m.group(0)
            return _normalize_date_str(raw, fmt)
    return ""


def _normalize_date_str(raw: str, fmt: str) -> str:
    """Convert a detected date string to YYYY-MM-DD."""
    try:
        from datetime import datetime
        raw = raw.strip().rstrip(",")
        if fmt == "%Y-%m-%d":
            # Already ISO-ish, just fix separators
            raw = raw.replace("/", "-")
            parts = raw.split("-")
            if len(parts) == 3:
                y, mo, d = int(parts[0]), int(parts[1]), int(parts[2])
                return f"{y:04d}-{mo:02d}-{d:02d}"
        elif fmt == "dmy":
            raw = raw.replace("/", "-")
            parts = raw.split("-")
            if len(parts) == 3:
                d, mo, y = int(parts[0]), int(parts[1]), int(parts[2])
                return f"{y:04d}-{mo:02d}-{d:02d}"
        elif fmt == "dmy_alt":
            # "21 June 2025" or "21 Jun 2025"
            parts = raw.split()
            if len(parts) == 3:
                d = int(parts[0])
                mo_str = parts[1][:3]
                y = int(parts[2])
                dt = datetime.strptime(f"{d} {mo_str} {y}", "%d %b %Y")
                return dt.strftime("%Y-%m-%d")
        elif fmt == "mdy":
            dt = datetime.strptime(raw, "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return ""


def _fallback_date_currency(pages_text: list, result) -> None:
    """Fill empty date/currency from text using regex detection.
    
    Scans ALL pages (not just first 2) to find date and currency.
    Mutates the ExtractionResult in-place.
    """
    # Combine all pages of text for detection
    combined = "\n".join(pages_text)
    
    # Treat "unknown" as empty for date and currency
    if result.date and result.date.lower() == "unknown":
        result.date = ""
    if result.currency and result.currency.lower() == "unknown":
        result.currency = ""
    
    if not result.date:
        detected = _detect_date(combined)
        if detected:
            result.date = detected
            result.warnings.append(f"Date detected from text: {detected}")
    
    if not result.currency:
        detected = _detect_currency(combined)
        if detected:
            result.currency = detected
            result.warnings.append(f"Currency detected from text: {detected}")
    
    # Also check per-item currency if result still empty
    if not result.currency:
        for item in result.items:
            if item.get("currency"):
                result.currency = item["currency"]
                break


@dataclass
class ExtractionResult:
    """Unified result from any extraction method.
    
    Attributes:
        items: List of extracted items
        supplier: Detected supplier name
        date: Detected quotation date (YYYY-MM-DD)
        currency: Detected currency code
        document_type: Document type (QUO/PO/PL/unknown)
        extraction_method: Which method was used (local/llm)
        warnings: Any warnings from the extraction process
        llm_warnings: LLM-specific warnings
    """
    items: list = field(default_factory=list)
    supplier: str = ""
    date: str = ""
    currency: str = ""
    document_type: str = "unknown"
    extraction_method: str = "local"
    warnings: list = field(default_factory=list)
    llm_warnings: list = field(default_factory=list)


async def extract_items_async(
    parse_result: dict,
    mode: str = "llm_first",
) -> ExtractionResult:
    """Extract items from a parse result using the specified mode.
    
    Args:
        parse_result: Output from parse_file_with_ocr()
        mode: Extraction mode (llm_first/local_first/llm_only/local_only)
        
    Returns:
        ExtractionResult with extracted items and metadata
    """
    # Extract text pages for LLM
    pages = parse_result.get("parsers", {}).get("pdfplumber", {}).get("pages", [])
    pages_text = [p.get("text", "") for p in pages]
    
    # Try local extraction
    local_result = local_extract(parse_result)
    local_items = local_result.get("items", [])
    
    # Route based on mode, then apply date/currency fallback
    result = None
    
    if mode == "local_only":
        result = ExtractionResult(
            items=local_items,
            supplier=local_result.get("supplier", ""),
            date=local_result.get("date", ""),
            currency=local_result.get("currency", ""),
            document_type=local_result.get("document_type", "unknown"),
            extraction_method="local",
            warnings=local_result.get("extraction_warnings", []),
        )
    
    elif mode == "llm_only":
        llm_result = await normalize_pages_with_llm(pages_text)
        if llm_result and llm_result.get("items"):
            result = ExtractionResult(
                items=llm_result["items"],
                supplier=llm_result.get("supplier", ""),
                date=llm_result.get("date", ""),
                currency=llm_result.get("currency", ""),
                document_type=llm_result.get("document_type", "unknown"),
                extraction_method="llm",
                llm_warnings=llm_result.get("llm_warnings", []),
            )
        else:
            result = ExtractionResult(
                extraction_method="llm",
                llm_warnings=["LLM extraction failed or returned no items"],
            )
    
    elif mode == "llm_first":
        llm_result = await normalize_pages_with_llm(pages_text)
        if llm_result and llm_result.get("items"):
            result = ExtractionResult(
                items=llm_result["items"],
                supplier=llm_result.get("supplier", ""),
                date=llm_result.get("date", ""),
                currency=llm_result.get("currency", ""),
                document_type=llm_result.get("document_type", "unknown"),
                extraction_method="llm",
                llm_warnings=llm_result.get("llm_warnings", []),
            )
        else:
            # LLM failed, fall back to local
            result = ExtractionResult(
                items=local_items,
                supplier=local_result.get("supplier", ""),
                date=local_result.get("date", ""),
                currency=local_result.get("currency", ""),
                document_type=local_result.get("document_type", "unknown"),
                extraction_method="local",
                warnings=local_result.get("extraction_warnings", []),
                llm_warnings=["LLM failed, using local extraction"],
            )
    
    else:  # mode == "local_first"
        if local_items:
            result = ExtractionResult(
                items=local_items,
                supplier=local_result.get("supplier", ""),
                date=local_result.get("date", ""),
                currency=local_result.get("currency", ""),
                document_type=local_result.get("document_type", "unknown"),
                extraction_method="local",
                warnings=local_result.get("extraction_warnings", []),
            )
        else:
            # Local returned 0 items, try LLM as fallback
            llm_result = await normalize_pages_with_llm(pages_text)
            if llm_result and llm_result.get("items"):
                result = ExtractionResult(
                    items=llm_result["items"],
                    supplier=llm_result.get("supplier", ""),
                    date=llm_result.get("date", ""),
                    currency=llm_result.get("currency", ""),
                    document_type=llm_result.get("document_type", "unknown"),
                    extraction_method="llm",
                    llm_warnings=llm_result.get("llm_warnings", []),
                )
            else:
                result = ExtractionResult(
                    items=local_items,
                    supplier=local_result.get("supplier", ""),
                    date=local_result.get("date", ""),
                    currency=local_result.get("currency", ""),
                    document_type=local_result.get("document_type", "unknown"),
                    extraction_method="local",
                    warnings=local_result.get("extraction_warnings", []),
                    llm_warnings=["Both local and LLM extraction failed"],
                )
    
    # Post-processing: detect date/currency from text if still empty
    _fallback_date_currency(pages_text, result)
    
    # Log extraction result
    logger.info("Extraction routed", extra={
        'category': 'AI',
        'mode': mode,
        'method': result.extraction_method,
        'items': len(result.items),
        'fallback': len(result.llm_warnings) > 0
    })
    
    return result
