"""
backend/extraction/llm.py — LLM-based quotation item extractor.

Calls the AI server (text mode, not image mode) to extract items from
quotation documents. Text mode is cheaper than image-mode VLM calls:
a typical quotation PDF is <3,000 chars of text, which fits in the
4,096-token context window of small models like qwen3-vl-4b.

The output is normalized to the same shape as the rules-based extractor
(items = [{brand, model, description, quantity, unit, unit_price, total, remark}])
so callers can use either extractor interchangeably.

This module is part of the extraction package and can be used standalone
or as the primary/fallback method in the extraction router.
"""
import json
import re
import base64
from pathlib import Path

import httpx


# A compact prompt that fits in the small context window of qwen3-vl-4b.
# Hybrid approach: detect headers first, fall back to content inference.
_TEXT_NORMALIZE_PROMPT = """Extract items from this document (quotation, purchase order, or price list). Return ONLY valid JSON.

═══ STEP 1: IDENTIFY TABLE COLUMNS ═══

The document text has tables with columns separated by ' | '. First, identify the column layout:

OPTION A — HEADER ROW DETECTED:
Look for a row containing column headers. Map each header to its field:

| Header Keyword | Field Name |
|----------------|------------|
| Item, No, #, Line | item_number |
| Brand, Manufacturer, Make | brand |
| Model, Part No, P/N, SKU, Code | model |
| Description, Desc, Product, Name | description |
| Qty, QTY, Quantity, Q'ty | quantity |
| Unit, UOM, Unit Type | unit |
| Price, Unit Price, Rate, Cost | unit_price |
| Total, Amount, Ext. Price | total |

OPTION B — NO HEADER ROW (content-based inference):
Use these rules to identify columns by content:
- Starts with numbers like "1", "1.1", "A" → Item number column
- Contains currency symbol ($, HKD, USD) or decimal price (16.60) → Price column
- Contains formatted total (1,234.56 with comma separator) → Total column
- Small integer (1-999) followed by text unit (pcs, set, m) → Quantity column
- Short text like "pcs", "set", "m", "lot", "pair" → Unit column
- Alphanumeric code with letters+numbers (e.g., "CZ7270-000", "SRP-X700") → Model column
- Long text with product details → Description column
- Brand names (Sony, QSC, Crestron) → Brand column

═══ STEP 2: EXTRACT ITEMS ═══

STRICT ITEM FILTERING:
- A valid item MUST have: (a) model OR description, AND (b) a numeric price
- If document has no price column (some POs), valid item needs model/description + quantity
- IGNORE rows that are: category headers, subtotals, totals, discounts, "Optional" notes
- IGNORE rows that have ONLY a letter/number (like "A", "B", "1") with no other data

CATEGORY HEADERS — IGNORE:
- Single letters (A, B, C) or Roman numerals (I, II, III)
- Rows with only a description and NO model, NO price, NO quantity
- Examples: "Fiber Accessories", "Audio System", "Cabling Infrastructure"

WORK ITEMS — VALID (include them):
- Rows with description but no quantity (labor/service items)
- Examples: "Supply and install all accessories...", "Testing and commissioning"
- Set quantity="" and unit="" for these items

═══ STEP 3: FIELD RULES ═══

BRAND vs SUPPLIER (CRITICAL):
- BRAND = product manufacturer per-ITEM (e.g., Sony, QSC, Digisol)
- SUPPLIER = company ISSUING the document (e.g., "CHONG HING ELECTRICAL")
- NEVER confuse these. Brand is per-item, Supplier is per-document.

MODEL RULES:
- Extract ONLY ONE model per item
- "change to X" → use ONLY X
- "include in ..." → IGNORE the entire row

QUANTITY FIELD:
- Must be numeric: "2", "10", "5.5"
- If field contains text words (supply, install, include) → it's DESCRIPTION, not quantity
- Empty quantity is OK for work items

PRICE FIELD:
- Strip currency symbols ($, HK$, USD, etc.)
- Format: X,XXX.XX (comma as thousand separator, period as decimal)
- Example: "$16.60" → "16.60", "$63,080.00" → "63,080.00"

DOCUMENT TYPE: "QUO" (quotation), "PO" (purchase order), "PL" (price list), or "unknown".

═══ OUTPUT FORMAT ═══

Return this exact structure:
{{
  "document_type": "QUO" | "PO" | "PL" | "unknown",
  "supplier": "company name ISSUING the document (NOT the product brand)",
  "currency": "ISO 4217 code (HKD, USD, MOP, etc.)",
  "date": "YYYY-MM-DD",
  "items": [
    {{
      "brand": "product manufacturer (NOT supplier)",
      "model": "model/part number",
      "description": "full product description",
      "quantity": "numeric string (empty for work items)",
      "unit": "pcs, set, m, lot, etc.",
      "unit_price": "formatted: X,XXX.XX",
      "total": "formatted: X,XXX.XX",
      "remark": "any extra notes"
    }}
  ]
}}

═══ EXAMPLES ═══

Example 1 — Valid item with all fields:
"9.1 | CZ7270-000 | LC Duplex Adapter | 3800 | pcs | $16.60 | $63,080.00"
→ {{"model": "CZ7270-000", "description": "LC Duplex Adapter", "quantity": "3800", "unit": "pcs", "unit_price": "16.60", "total": "63,080.00"}}

Example 2 — Category header (IGNORE):
"9 | Fiber Accessories"
→ IGNORE (no model, no price)

Example 3 — Work item (valid, no quantity):
"1 | Supply and install all fiber accessories and equipment | | | | 15,000.00 | 15,000.00"
→ {{"description": "Supply and install all fiber accessories and equipment", "quantity": "", "unit": "", "unit_price": "15,000.00", "total": "15,000.00"}}

Return ONLY valid parseable JSON, no markdown, no explanation.

Text:
{text}
"""


import re as _re


# Table header patterns that pdfplumber merges with the first data row.
# These confuse the LLM because they look like item data.
_TABLE_HEADER_PATTERNS = [
    _re.compile(r"ITEM\s+BRAND\s+MODEL\s+DESCRIPTIONS?\s+QTY\s+.*", _re.IGNORECASE),
    _re.compile(r"ITEM\s+BRAND\s+MODEL\s+DESCRIPTIONS?\s+.*", _re.IGNORECASE),
    _re.compile(r"NO\.?\s+BRAND\s+MODEL\s+DESCRIPTIONS?\s+.*", _re.IGNORECASE),
    _re.compile(r"BRAND\s+MODEL\s+DESCRIPTIONS?\s+QTY\s+.*", _re.IGNORECASE),
    _re.compile(r"BRAND\s+MODEL\s+DESCRIPTION\s+QTY\s+.*", _re.IGNORECASE),
    _re.compile(r"UNIT\s+PRICE\s+TOTAL\s+PRICE.*", _re.IGNORECASE),
    _re.compile(r"HK\s+\(HKD\)\s+HK\s+\(HKD\)", _re.IGNORECASE),
    _re.compile(r"\(HKD\)\s+\(HKD\)", _re.IGNORECASE),
]


def _strip_table_headers(text):
    """Remove table header rows that get merged with data by pdfplumber.
    
    These headers (e.g. 'ITEM BRAND MODEL DESCRIPTIONS QTY...')
    confuse the LLM because they look like the first item row.
    """
    if not text:
        return text
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        skip = False
        for pattern in _TABLE_HEADER_PATTERNS:
            if pattern.search(stripped):
                skip = True
                break
        if not skip:
            cleaned.append(line)
    return "\n".join(cleaned)


def _format_text_for_llm(text, max_chars=8000):
    """Trim text to fit in the small model's context window.
    Prefer the first chunk (most of the metadata + first table is there).
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... text truncated for context window ...]"


def _normalize_price(s):
    """Strip currency, ensure X,XXX.XX format."""
    if not s:
        return ""
    s = str(s)
    s = s.replace("HK$", "").replace("US$", "")
    s = s.replace("$", "").replace("€", "").replace("£", "")
    s = s.replace("¥", "").replace("￥", "")
    s = re.sub(
        r"\b(HKD|USD|EUR|GBP|CNY|RMB|JPY|HK|US|MOP)\b",
        "", s, flags=re.IGNORECASE,
    )
    s = s.replace(" ", "").replace(",", "")
    try:
        num = float(s)
        if num == int(num):
            return f"{int(num):,}"
        return f"{num:,.2f}"
    except (ValueError, TypeError):
        return ""


def _normalize_item(item):
    """Normalize a single LLM-extracted item to our schema."""
    if not isinstance(item, dict):
        return None
    
    # Handle quantity: if "3800 pcs", split to qty="3800", unit="pcs"
    qty_raw = str(item.get("quantity", "") or "").strip()
    unit_raw = str(item.get("unit", "") or "").strip()
    
    if qty_raw and not unit_raw:
        # Check if quantity contains unit (e.g., "3800 pcs")
        qty_match = re.match(r'^([\d,.]+)\s*([a-zA-Z]+)$', qty_raw)
        if qty_match:
            qty_raw = qty_match.group(1)
            unit_raw = qty_match.group(2)
    
    out = {
        "brand": str(item.get("brand", "") or "").strip(),
        "model": str(item.get("model", "") or "").strip(),
        "description": str(item.get("description", "") or "").strip(),
        "quantity": qty_raw,
        "unit": unit_raw,
        "unit_price": _normalize_price(item.get("unit_price", "")),
        "total": _normalize_price(item.get("total", "")),
        "remark": str(item.get("remark", "") or "").strip(),
    }
    if not out["model"] and not out["description"]:
        return None
    if not out["unit_price"] and not out["total"]:
        return None
    if not out["model"] and out["description"]:
        out["model"] = out["description"]
    return out


def _validate_item(item):
    """Post-extraction validation to fix common LLM errors.
    
    Returns validated item, or None if item should be filtered out.
    """
    if not item:
        return None
    
    # 1. Validate quantity — must be numeric if provided
    qty = item.get("quantity", "")
    if qty:
        clean_qty = qty.replace(",", "").replace(" ", "").strip()
        if not re.match(r'^\d+(\.\d+)?$', clean_qty):
            # Quantity contains text — likely a description, not quantity
            # Move to remark and clear quantity
            remark = item.get("remark", "")
            qty_info = f"Qty was: {qty}"
            item["remark"] = f"{remark} | {qty_info}".strip(" |") if remark else qty_info
            item["quantity"] = ""
    
    # 2. Validate unit_price — must be numeric if provided
    price = item.get("unit_price", "")
    if price:
        clean_price = price.replace(",", "").replace(" ", "").replace("$", "").strip()
        try:
            float(clean_price)
        except ValueError:
            # Price is not numeric — cannot validate
            pass
    
    # 3. Check for category headers (no model, no price, no quantity)
    has_model = bool(item.get("model", "").strip())
    has_price = bool(item.get("unit_price", "").strip()) or bool(item.get("total", "").strip())
    has_qty = bool(item.get("quantity", "").strip())
    has_desc = bool(item.get("description", "").strip())
    
    # If only description and nothing else — likely a category header
    if has_desc and not has_model and not has_price and not has_qty:
        desc_lower = item.get("description", "").lower()
        # Check if it looks like a category header (short, no specific product info)
        if len(desc_lower.split()) < 6:
            return None
    
    return item


def _validate_items(items):
    """Validate and filter a list of extracted items."""
    validated = []
    for item in items:
        result = _validate_item(item)
        if result:
            validated.append(result)
    return validated


async def normalize_text_with_llm(text, cfg=None):
    """Call the LLM with text input. Returns:
        (result_dict, error_string)

    result_dict shape (compatible with extract_items output):
        {
            "document_type": "QUO" | "PO" | "PL" | "unknown",
            "supplier": "...",
            "currency": "...",
            "date": "YYYY-MM-DD" or "",
            "items": [normalized items],
            "llm_warnings": [...],
        }

    Returns ({}, error_string) on failure.
    """
    # Import from backend.utils to avoid circular dependency with main.py
    from ..utils import repair_json_quotes, load_config

    if cfg is None:
        cfg = load_config()
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    timeout = cfg.get("timeout", 90)
    max_retries = cfg.get("max_retries", 2)

    if not endpoint or not model:
        return {}, "AI endpoint or model not configured"

    # Strip table headers that confuse the LLM
    text = _strip_table_headers(text)
    
    formatted = _format_text_for_llm(text)
    if not formatted.strip():
        return {}, "No text to send to LLM"

    prompt = _TEXT_NORMALIZE_PROMPT.format(text=formatted)
    messages = [{"role": "user", "content": prompt}]

    last_error = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=timeout, connect=5.0)) as client:
                resp = await client.post(
                    endpoint,
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": 2048,
                        "temperature": 0.1,
                    },
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if "choices" not in result or not result.get("choices"):
                        last_error = "AI returned no choices"
                        continue
                    msg = result["choices"][0]["message"]
                    raw = (msg.get("content") or msg.get("reasoning_content") or "").strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()
                    start = raw.find("{")
                    end = raw.rfind("}") + 1
                    if start == -1:
                        last_error = "No JSON in LLM response"
                        continue
                    json_str = raw[start:end] if end > start else raw[start:]
                    if end <= start:
                        json_str = json_str.rstrip(",") + "]}"
                    parsed = None
                    for candidate in (json_str, repair_json_quotes(json_str)):
                        try:
                            parsed = json.loads(candidate)
                            break
                        except json.JSONDecodeError:
                            continue
                    if parsed is None:
                        # Last-ditch: try a few suffixes
                        for suffix in ["}", "\"}]}", "}]}", "]}"]:
                            try:
                                parsed = json.loads(json_str.rstrip(",") + suffix)
                                break
                            except json.JSONDecodeError:
                                continue
                    if parsed is None:
                        last_error = f"Could not parse LLM JSON: {raw[:200]}"
                        continue
                    # Normalize and validate
                    raw_items = []
                    for it in (parsed.get("items") or []):
                        n = _normalize_item(it)
                        if n:
                            raw_items.append(n)
                    items = _validate_items(raw_items)
                    return {
                        "document_type": parsed.get("document_type", "unknown"),
                        "supplier": str(parsed.get("supplier", "") or "").strip(),
                        "currency": str(parsed.get("currency", "") or "").strip(),
                        "date": str(parsed.get("date", "") or "").strip(),
                        "items": items,
                        "llm_warnings": [],
                    }, None
                else:
                    last_error = f"AI returned HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.ConnectError as e:
            last_error = f"Connection error: {e}"
        except httpx.TimeoutException:
            last_error = "Timeout"
        except Exception as e:
            last_error = f"Error: {e}"
        if attempt < max_retries - 1:
            print(f"LLM normalize attempt {attempt + 1} failed: {last_error}, retrying...")

    return {}, last_error or "All retries exhausted"


async def normalize_pages_with_llm(pages_text: list, cfg=None):
    """Process a multi-page document by calling the LLM once per page and
    merging the results.

    v0.038.0 fix: the previous code concatenated all pages into one text
    blob and called the LLM once. The LLM consistently latched onto the
    LAST page's table and ignored items from earlier pages, so multi-page
    scanned PDFs only got items from one page. By processing each page
    separately, the LLM sees a clean context per page and reliably returns
    items from every page.

    Returns a dict in the same shape as normalize_text_with_llm:
        {
            "document_type": ...,
            "supplier": ...,
            "currency": ...,
            "date": ...,
            "items": [...],   # each item is tagged with "page" (1-indexed)
            "per_page_errors": [...],   # populated on partial failure
        }
    """
    # Import from backend.utils to avoid circular dependency with main.py
    from ..utils import load_config

    if cfg is None:
        cfg = load_config()

    all_items = []
    suppliers: list = []
    dates: list = []
    currencies: list = []
    document_types: list = []
    per_page_errors: list = []

    for page_idx, page_text in enumerate(pages_text, start=1):
        if not page_text or not page_text.strip():
            per_page_errors.append(f"Page {page_idx}: No extractable text (scanned/image page?)")
            continue
        result, err = await normalize_text_with_llm(page_text, cfg=cfg)
        if err:
            per_page_errors.append(f"Page {page_idx}: {err}")
            continue
        # Tag each item with its source page so the streaming endpoint can
        # report per-page progress.
        for it in result.get("items", []):
            it["page"] = page_idx
            all_items.append(it)
        if result.get("supplier"):
            suppliers.append(result["supplier"])
        if result.get("date"):
            dates.append(result["date"])
        if result.get("currency"):
            currencies.append(result["currency"])
        if result.get("document_type") and result["document_type"] != "unknown":
            document_types.append(result["document_type"])

    # Merge metadata. Use the first non-empty value for each field. In
    # a typical multi-page document all pages have the same supplier/
    # date/currency; in the rare case they differ (e.g. a multi-vendor
    # compilation) the first page wins, which is the conventional choice.
    supplier = suppliers[0] if suppliers else ""
    shared_date = dates[0] if dates else ""
    currency = currencies[0] if currencies else ""
    document_type = document_types[0] if document_types else "unknown"

    return {
        "supplier": supplier,
        "date": shared_date,
        "currency": currency,
        "document_type": document_type,
        "items": all_items,
        "per_page_errors": per_page_errors,
    }
