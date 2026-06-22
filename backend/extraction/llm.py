"""
backend/extraction/llm.py — Text-based LLM extraction for PDFs with text.

Sends extracted page text to the LLM with a minimal prompt.
Used when pdfplumber successfully extracts text from a PDF
(or when processing XLSX files via openpyxl text).

Same simple prompt as vision.py, but text-mode (no images).
"""
import json
import logging

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..utils import normalize_date

logger = logging.getLogger(__name__)


# ─── Simple Prompt (same shape as vision.py) ─────────────────
EXTRACT_PROMPT = """Extract items from this purchase order or quotation.

Return JSON with:
- document_type: "QUO" for quotation, "PO" for purchase order, "PL" for packing list, or "unknown"
- supplier: company issuing the document
- date: the date as shown in the document (we will parse it)
- currency: HKD/USD/etc
- items: list of objects

Each item:
- brand: manufacturer name, or empty string if not listed
- model: part/model number
- description: product name
- quantity: number only
- unit: pcs/m/set/etc
- unit_price: number only, no commas or symbols
- total: number only, no commas or symbols

Rules:
- Each row in the table is one item
- Rows without prices are section headers — SKIP them
- "Total" or "Subtotal" rows — SKIP them entirely
- Return ONLY valid JSON, no other text

Document text:
{text}"""


# ─── XLSX-specific prompt ─────────────────────────────────────
# Spreadsheets are pipe-delimited (|). The first row is always
# the column header — the model uses it to identify each column.
EXTRACT_PROMPT_XLSX = """Extract items from this spreadsheet quotation.

The data is pipe-delimited (|). The first row is the column header —
use it to identify what each column represents.

Return JSON with:
- document_type: "QUO" for quotation, "PO" for purchase order, "PL" for packing list, or "unknown"
- supplier: company issuing the document
- date: the date as shown in the document
- currency: HKD/USD/etc
- items: list of objects

Each item:
- brand: manufacturer name, or empty string if not listed
- model: part/model number
- description: product name
- quantity: number only
- unit: pcs/m/set/etc
- unit_price: number only, no commas or symbols (if available)
- total: number only, no commas or symbols (if available)

Rules:
- Each row is one item
- If the row has a unit price OR a total price, include it as an item
- Only skip if BOTH unit price and total are blank (likely a section header)
- "Total" or "Subtotal" rows — SKIP them entirely
- Return ONLY valid JSON, no other text

Document text:
{text}"""


# ─── Pydantic Validation Models ────────────────────────────


class ExtractionItem(BaseModel):
    """Single line item from an extraction result.

    Every field is a string; anything the LLM returns (None, number, etc.)
    is coerced and stripped. This gives downstream code a consistent type
    contract instead of raw dict access.
    """

    brand: str = ""
    model: str = ""
    description: str = ""
    quantity: str = ""
    unit: str = ""
    unit_price: str = ""
    total: str = ""
    remark: str = ""

    def __init__(self, **data):
        # Coerce every value to a stripped string before Pydantic sees it.
        # This handles None, int, float, bool, etc. that the LLM might return.
        coerced = {}
        for k, v in data.items():
            if k in self.model_fields:
                coerced[k] = str(v).strip() if v is not None else ""
        super().__init__(**coerced)


class ExtractionResult(BaseModel):
    """Top-level extraction result from the LLM.

    Validates the overall shape and delegates item validation to ExtractionItem.
    """

    document_type: str = "unknown"
    supplier: str = ""
    date: str = ""
    currency: str = ""
    items: list[ExtractionItem] = Field(default_factory=list)

    def __init__(self, **data):
        # Coerce top-level string fields the same way
        for key in ("document_type", "supplier", "date", "currency"):
            v = data.get(key)
            if v is not None and not isinstance(v, str):
                data[key] = str(v)
            elif v is None:
                data[key] = ""
        # Ensure items is a list
        if "items" not in data or data["items"] is None:
            data["items"] = []
        super().__init__(**data)


def _clean_item(item):
    if not isinstance(item, dict):
        return None
    cleaned = {}
    for key in ("brand", "model", "description", "quantity", "unit",
                "unit_price", "total", "remark"):
        val = item.get(key)
        cleaned[key] = str(val).strip() if val else ""
    if not cleaned["model"] and not cleaned["description"] and not cleaned["unit_price"]:
        return None
    return cleaned


async def _call_llm(text: str, cfg: dict, is_xlsx: bool = False) -> dict:
    """Make a single LLM call and return parsed result.

    Returns dict with keys: document_type, supplier, currency, date,
    items, llm_warnings.
    """
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    timeout = cfg.get("timeout", 120)
    max_retries = cfg.get("max_retries", 2)
    max_tokens = 8192 if is_xlsx else 4096

    prompt = (EXTRACT_PROMPT_XLSX if is_xlsx else EXTRACT_PROMPT).format(text=text)
    messages = [{"role": "user", "content": prompt}]

    last_error = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout=timeout, connect=10.0),
            ) as client:
                resp = await client.post(
                    endpoint,
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.1,
                    },
                )

            if resp.status_code == 200:
                result = resp.json()
                if not result.get("choices"):
                    last_error = "AI returned no choices"
                    continue

                msg = result["choices"][0]["message"]
                raw = (msg.get("content") or "").strip()

                # Strip markdown code blocks
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

                # Find JSON
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start == -1 or end <= start:
                    last_error = "No JSON in response"
                    continue

                try:
                    parsed = json.loads(raw[start:end])
                except json.JSONDecodeError:
                    last_error = "Could not parse JSON"
                    continue

                # Validate LLM output against Pydantic model.
                # This catches type errors, missing fields, and malformed
                # structures that would otherwise cause obscure 500s downstream.
                try:
                    validated = ExtractionResult.model_validate(parsed)
                except ValidationError as e:
                    last_error = f"LLM output validation failed: {e}"
                    continue

                # Filter items that have no meaningful content
                # (same logic as _clean_item's empty-item filter)
                items = [
                    item.model_dump()
                    for item in validated.items
                    if item.model or item.description or item.unit_price
                ]

                return {
                    "document_type": validated.document_type or "unknown",
                    "supplier": validated.supplier or "",
                    "currency": validated.currency or "",
                    "date": normalize_date(validated.date or ""),
                    "items": items,
                    "llm_warnings": [],
                }

            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"

        except httpx.ConnectError as e:
            last_error = f"Connection error: {e}"
        except httpx.TimeoutException:
            last_error = "Timeout"
        except Exception as e:
            logger.warning("LLM extraction retry failed (caution: may contain doc data)", exc_info=True)
            last_error = f"Error: {e}"

    return {"supplier": "", "date": "", "currency": "",
            "document_type": "unknown", "items": [],
            "llm_warnings": [f"LLM extraction failed: {last_error}"]}


async def normalize_pages_with_llm(pages_text: list, cfg: dict = None, is_xlsx: bool = False) -> dict:
    """Extract items from text pages using the LLM.

    Args:
        pages_text: List of text strings, one per page
        cfg: Config dict (loaded from config.json if None)
        is_xlsx: True if the source is an XLSX file (uses XLSX-specific prompt)

    Returns:
        dict with keys: document_type, supplier, currency, date, items, llm_warnings
    """
    from ..utils import load_config

    if cfg is None:
        cfg = load_config()

    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")

    if not endpoint or not model:
        return {"supplier": "", "date": "", "currency": "",
                "document_type": "unknown", "items": [],
                "llm_warnings": ["AI endpoint or model not configured"]}

    # Filter to non-empty pages
    non_empty = [t for t in pages_text if t.strip()]
    if not non_empty:
        return {"supplier": "", "date": "", "currency": "",
                "document_type": "unknown", "items": [],
                "llm_warnings": ["No text content to extract from"]}

    # XLSX: process each sheet separately to avoid token overflow.
    # PDF: combine all pages into one call (fewer pages, fits in 4096 tokens).
    if is_xlsx:
        all_items = []
        all_warnings = []
        doc_type = "unknown"
        supplier = ""
        currency = ""
        date = ""

        for i, text in enumerate(non_empty):
            result = await _call_llm(text, cfg, is_xlsx=True)
            all_items.extend(result.get("items", []))
            all_warnings.extend(result.get("llm_warnings", []))
            # Use metadata from first sheet that provides it
            if doc_type == "unknown" and result.get("document_type", "unknown") != "unknown":
                doc_type = result["document_type"]
            if not supplier and result.get("supplier"):
                supplier = result["supplier"]
            if not currency and result.get("currency"):
                currency = result["currency"]
            if not date and result.get("date"):
                date = result["date"]

        return {
            "document_type": doc_type,
            "supplier": supplier,
            "currency": currency,
            "date": date,
            "items": all_items,
            "llm_warnings": all_warnings,
        }

    # PDF: combine all pages into one call
    combined = "\n\n=== Page {}\n".join(
        text for text in non_empty if text.strip()
    )
    return await _call_llm(combined, cfg, is_xlsx=False)
