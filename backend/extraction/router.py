"""
backend/extraction/router.py — Unified extraction router for QuoteHub.

Single "auto" mode that selects the best extraction path:

  PDF with text → Text LLM (fast, no images)
  Scanned PDF → Vision LLM (image → JSON, 1 call/page)
  XLSX         → Text LLM (openpyxl text)
  Any fail     → Local rules (fallback)

No user-facing mode selection. The system decides.
"""
import logging
from dataclasses import dataclass, field

from .local import extract_items as local_extract
from .llm import normalize_pages_with_llm
from .vision import extract_with_vision

logger = logging.getLogger(__name__)


# Threshold: if avg text chars per page > this, use Text LLM
# (pdfplumber found real content). Below this → scanned → Vision LLM.
SCANNED_PDF_THRESHOLD = 50


@dataclass
class ExtractionResult:
    """Unified result from any extraction method.

    Attributes:
        items: List of extracted items
        supplier: Detected supplier name
        date: Detected date (YYYY-MM-DD, normalized)
        currency: Detected currency code
        document_type: Document type (QUO/PO/PL/unknown)
        extraction_method: Which method was used (vision/llm/local)
        warnings: Any warnings from the extraction process
    """
    items: list = field(default_factory=list)
    supplier: str = ""
    date: str = ""
    currency: str = ""
    document_type: str = "unknown"
    extraction_method: str = "local"
    warnings: list = field(default_factory=list)


async def extract_items_async(parse_result: dict) -> ExtractionResult:
    """Extract items from a parse result using auto-selected method.

    Args:
        parse_result: Output from parse_file_with_ocr()

    Returns:
        ExtractionResult with extracted items and metadata
    """
    from ..utils import load_config

    cfg = load_config()
    extraction_enabled = cfg.get("extraction_enabled", True)

    if not extraction_enabled:
        logger.info("Extraction disabled by config, using local-only")
        return _run_local(parse_result)

    # ── Detect file type and available text ──────────────────
    pdf_path = parse_result.get("pdf_path", "")
    is_xlsx = parse_result.get("parsers", {}).get("xlsx", {}).get("available", False)

    pages_text = _get_pages_text(parse_result)
    total_chars = sum(len(t) for t in pages_text)
    num_pages = parse_result.get("num_pages", 1)
    avg_chars = total_chars / num_pages if num_pages else 0

    is_scanned = avg_chars < SCANNED_PDF_THRESHOLD

    logger.info("Extraction auto-detect", extra={
        'category': 'AI',
        'type': 'xlsx' if is_xlsx else ('scanned' if is_scanned else 'text'),
        'pages': num_pages,
        'avg_chars': round(avg_chars, 1),
    })

    # ── Route ────────────────────────────────────────────────
    result = None

    if is_xlsx or not is_scanned:
        # Text-mode: XLSX or PDF with extractable text
        result = await _try_text_llm(pages_text, cfg, is_xlsx=is_xlsx)

    if result is None:
        # Scanned PDF or text LLM failed → try Vision LLM
        result = await _try_vision_llm(pdf_path, cfg)

    if result is None:
        # Both AI methods failed → fall back to local
        result = _run_local(parse_result)

    # Log result
    logger.info("Extraction complete", extra={
        'category': 'AI',
        'method': result.extraction_method,
        'items': len(result.items),
    })

    return result


# ─── Internal helpers ─────────────────────────────────────────


def _get_pages_text(parse_result: dict) -> list[str]:
    """Extract per-page text from parse result."""
    pages = parse_result.get("parsers", {}).get("pdfplumber", {}).get("pages", [])
    return [p.get("text", "") for p in pages]


def _run_local(parse_result: dict) -> ExtractionResult:
    """Run local rules-based extraction."""
    local_result = local_extract(parse_result)
    return ExtractionResult(
        items=local_result.get("items", []),
        supplier=local_result.get("supplier", ""),
        date=local_result.get("date", ""),
        currency=local_result.get("currency", ""),
        document_type=local_result.get("document_type", "unknown"),
        extraction_method="local",
        warnings=local_result.get("extraction_warnings", []),
    )


async def _try_text_llm(pages_text: list, cfg: dict, is_xlsx: bool = False) -> ExtractionResult | None:
    """Try Text LLM extraction. Returns None if it fails."""
    if not pages_text or not any(t.strip() for t in pages_text):
        return None

    result = await normalize_pages_with_llm(pages_text, cfg, is_xlsx=is_xlsx)
    if result and result.get("items"):
        return ExtractionResult(
            items=result["items"],
            supplier=result.get("supplier", ""),
            date=result.get("date", ""),
            currency=result.get("currency", ""),
            document_type=result.get("document_type", "unknown"),
            extraction_method="llm",
            warnings=result.get("llm_warnings", []),
        )
    return None


async def _try_vision_llm(pdf_path: str, cfg: dict) -> ExtractionResult | None:
    """Try Vision LLM extraction. Returns None if it fails."""
    if not pdf_path:
        return None

    result = await extract_with_vision(pdf_path, cfg)
    if result and result.get("items"):
        return ExtractionResult(
            items=result["items"],
            supplier=result.get("supplier", ""),
            date=result.get("date", ""),
            currency=result.get("currency", ""),
            document_type=result.get("document_type", "unknown"),
            extraction_method="vision",
            warnings=result.get("warnings", []),
        )
    return None
