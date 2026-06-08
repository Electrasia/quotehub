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

from dataclasses import dataclass, field
from typing import Optional

from .local import extract_items as local_extract
from .llm import normalize_pages_with_llm


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
    ocr_enabled: bool = True,
) -> ExtractionResult:
    """Extract items from a parse result using the specified mode.
    
    Args:
        parse_result: Output from parse_file_with_ocr()
        mode: Extraction mode (llm_first/local_first/llm_only/local_only)
        ocr_enabled: Whether OCR was enabled during parsing
        
    Returns:
        ExtractionResult with extracted items and metadata
    """
    # Extract text pages for LLM
    pages = parse_result.get("parsers", {}).get("pdfplumber", {}).get("pages", [])
    pages_text = [p.get("text", "") for p in pages]
    
    # Try local extraction
    local_result = local_extract(parse_result)
    local_items = local_result.get("items", [])
    
    # Route based on mode
    if mode == "local_only":
        return ExtractionResult(
            items=local_items,
            supplier=local_result.get("supplier", ""),
            date=local_result.get("date", ""),
            currency=local_result.get("currency", ""),
            document_type=local_result.get("document_type", "unknown"),
            extraction_method="local",
            warnings=local_result.get("extraction_warnings", []),
        )
    
    if mode == "llm_only":
        llm_result = await normalize_pages_with_llm(pages_text)
        if llm_result and llm_result.get("items"):
            return ExtractionResult(
                items=llm_result["items"],
                supplier=llm_result.get("supplier", ""),
                date=llm_result.get("date", ""),
                currency=llm_result.get("currency", ""),
                document_type=llm_result.get("document_type", "unknown"),
                extraction_method="llm",
                llm_warnings=llm_result.get("llm_warnings", []),
            )
        # LLM failed, return empty result (no fallback in llm_only mode)
        return ExtractionResult(
            extraction_method="llm",
            llm_warnings=["LLM extraction failed or returned no items"],
        )
    
    if mode == "llm_first":
        # Try LLM first
        llm_result = await normalize_pages_with_llm(pages_text)
        if llm_result and llm_result.get("items"):
            return ExtractionResult(
                items=llm_result["items"],
                supplier=llm_result.get("supplier", ""),
                date=llm_result.get("date", ""),
                currency=llm_result.get("currency", ""),
                document_type=llm_result.get("document_type", "unknown"),
                extraction_method="llm",
                llm_warnings=llm_result.get("llm_warnings", []),
            )
        # LLM failed, fall back to local
        return ExtractionResult(
            items=local_items,
            supplier=local_result.get("supplier", ""),
            date=local_result.get("date", ""),
            currency=local_result.get("currency", ""),
            document_type=local_result.get("document_type", "unknown"),
            extraction_method="local",
            warnings=local_result.get("extraction_warnings", []),
            llm_warnings=["LLM failed, using local extraction"],
        )
    
    # mode == "local_first" (default behavior)
    if local_items:
        return ExtractionResult(
            items=local_items,
            supplier=local_result.get("supplier", ""),
            date=local_result.get("date", ""),
            currency=local_result.get("currency", ""),
            document_type=local_result.get("document_type", "unknown"),
            extraction_method="local",
            warnings=local_result.get("extraction_warnings", []),
        )
    
    # Local returned 0 items, try LLM as fallback
    llm_result = await normalize_pages_with_llm(pages_text)
    if llm_result and llm_result.get("items"):
        return ExtractionResult(
            items=llm_result["items"],
            supplier=llm_result.get("supplier", ""),
            date=llm_result.get("date", ""),
            currency=llm_result.get("currency", ""),
            document_type=llm_result.get("document_type", "unknown"),
            extraction_method="llm",
            llm_warnings=llm_result.get("llm_warnings", []),
        )
    
    # Both failed
    return ExtractionResult(
        items=local_items,
        supplier=local_result.get("supplier", ""),
        date=local_result.get("date", ""),
        currency=local_result.get("currency", ""),
        document_type=local_result.get("document_type", "unknown"),
        extraction_method="local",
        warnings=local_result.get("extraction_warnings", []),
        llm_warnings=["Both local and LLM extraction failed"],
    )
