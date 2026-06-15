"""
backend/extraction/llm.py — Text-based LLM extraction for PDFs with text.

Sends extracted page text to the LLM with a minimal prompt.
Used when pdfplumber successfully extracts text from a PDF
(or when processing XLSX files via openpyxl text).

Same simple prompt as vision.py, but text-mode (no images).
"""
import json

import httpx

from ..utils import normalize_date


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


def _clean_item(item):
    """Basic cleanup: strip whitespace. No validation."""
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
    timeout = cfg.get("timeout", 120)
    max_retries = cfg.get("max_retries", 2)

    if not endpoint or not model:
        return {"supplier": "", "date": "", "currency": "",
                "document_type": "unknown", "items": [],
                "llm_warnings": ["AI endpoint or model not configured"]}

    # Combine all page text
    combined = "\n\n=== Page {}\n".join(
        text for text in pages_text if text.strip()
    )

    if not combined.strip():
        return {"supplier": "", "date": "", "currency": "",
                "document_type": "unknown", "items": [],
                "llm_warnings": ["No text content to extract from"]}

    prompt = (EXTRACT_PROMPT_XLSX if is_xlsx else EXTRACT_PROMPT).format(text=combined)

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
                        "max_tokens": 4096,
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

                # Clean items
                items = []
                for item in parsed.get("items", []):
                    cleaned = _clean_item(item)
                    if cleaned:
                        items.append(cleaned)

                return {
                    "document_type": parsed.get("document_type", "unknown"),
                    "supplier": parsed.get("supplier", ""),
                    "currency": parsed.get("currency", ""),
                    "date": normalize_date(parsed.get("date", "")),
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
            last_error = f"Error: {e}"

    return {"supplier": "", "date": "", "currency": "",
            "document_type": "unknown", "items": [],
            "llm_warnings": [f"LLM extraction failed: {last_error}"]}
