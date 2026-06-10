"""
backend/extraction/__init__.py — Unified extraction interface for QuoteHub.

This package provides a modular extraction system with pluggable backends:
  - local: Rules-based extraction from pdfplumber tables and text
  - llm: LLM-based extraction using text-mode API calls

The router module handles mode selection and fallback logic.

Extraction Modes:
  - llm_first: Try LLM first, fall back to local if LLM fails
  - local_first: Try local first, fall back to LLM if local returns 0 items
  - llm_only: Only use LLM (no fallback)
  - local_only: Only use local rules (no fallback)

Usage:
    from backend.extraction import extract_items_async

    result = await extract_items_async(parse_result, mode="llm_first")
"""

from .router import extract_items_async, ExtractionResult

__all__ = ["extract_items_async", "ExtractionResult"]
