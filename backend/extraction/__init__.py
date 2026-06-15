"""
backend/extraction/__init__.py — Unified extraction interface for QuoteHub.

Single "auto" mode selects the best extraction path:
  - Vision LLM for scanned PDFs (image → JSON)
  - Text LLM for PDFs with text or XLSX files
  - Local rules as fallback

Usage:
    from backend.extraction import extract_items_async

    result = await extract_items_async(parse_result)
"""

from .router import extract_items_async, ExtractionResult

__all__ = ["extract_items_async", "ExtractionResult"]
