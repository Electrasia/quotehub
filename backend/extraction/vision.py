"""
backend/extraction/vision.py — Vision LLM extraction for scanned PDFs.

Sends each PDF page as an image to a Vision LLM (e.g., Qwen3-vl-4B) with
a minimal prompt. No post-processing — the model's raw JSON output is
returned directly and the user reviews it.

This module is used by the extraction router when pdfplumber returns
little or no text (scanned/image-only PDFs).
"""
import json
import base64
import io
import logging

import httpx
from PIL import Image
import fitz

from ..utils import normalize_date

logger = logging.getLogger(__name__)


# ─── Simple Prompt (used for every page) ───────────────────────
# Same prompt for all pages. Metadata fields (supplier, date, etc.)
# only appear on the first page; the model will leave them empty
# on continuation pages, which we ignore.
EXTRACT_PROMPT = """Extract items from this purchase order or quotation.

Return JSON with:
- document_type: "QUO" for quotation, "PO" for purchase order, "PL" for packing list, or "unknown"
- supplier: company issuing the document (only on first page; leave empty on continuation pages)
- date: the date as shown in the document (only on first page; leave empty on continuation pages)
- currency: HKD/USD/etc (only on first page; leave empty on continuation pages)
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
- Return ONLY valid JSON, no other text"""


RENDER_DPI = 200


def _render_page_to_image(pdf_path: str, page_num: int) -> bytes:
    """Render a PDF page to JPEG bytes at fixed 200 DPI."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_num]
        zoom = RENDER_DPI / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90, optimize=True)
        return buf.getvalue()
    finally:
        doc.close()


def _clean_item(item):
    """Basic cleanup: strip whitespace from strings. No validation."""
    if not isinstance(item, dict):
        return None
    cleaned = {}
    for key in ("brand", "model", "description", "quantity", "unit",
                "unit_price", "total", "remark"):
        val = item.get(key)
        cleaned[key] = str(val).strip() if val else ""
    # Only filter truly empty rows
    if not cleaned["model"] and not cleaned["description"] and not cleaned["unit_price"]:
        return None
    return cleaned


async def extract_with_vision(pdf_path: str, cfg: dict = None) -> dict:
    """Extract items from scanned PDF using Vision LLM.

    Processes each page separately, combines results.
    Returns raw items with light cleanup only.

    Args:
        pdf_path: Path to PDF file
        cfg: Config dict (loaded from config.json if None)

    Returns:
        dict with keys: document_type, supplier, currency, date, items, warnings
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
                "warnings": ["AI endpoint or model not configured"],
                "extraction_method": "vision"}

    # Get page count
    doc = fitz.open(pdf_path)
    num_pages = len(doc)
    doc.close()

    all_items = []
    supplier, doc_date, currency, doc_type = "", "", "", "unknown"
    warnings = []

    for page_idx in range(num_pages):
        try:
            img_bytes = _render_page_to_image(pdf_path, page_idx)
            b64 = base64.b64encode(img_bytes).decode("utf-8")

            messages = [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                     {"type": "text", "text": EXTRACT_PROMPT},
                ],
            }]

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

                        # Strip markdown code blocks if present
                        if raw.startswith("```"):
                            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                        if raw.endswith("```"):
                            raw = raw[:-3]
                        raw = raw.strip()

                        # Cap VLM response size to prevent OOM on runaway models.
                        # max_tokens=4096 limits the model to ~8KB, so 100KB is a
                        # generous safety fuse that never triggers in normal operation.
                        MAX_RESPONSE_BYTES = 100 * 1024  # 100 KB
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raw = raw[:MAX_RESPONSE_BYTES]
                            warnings.append(
                                f"Page {page_idx + 1}: VLM response truncated "
                                f"(exceeded {MAX_RESPONSE_BYTES} bytes)"
                            )

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

                        # Extract items
                        page_items = parsed.get("items", [])
                        for item in page_items:
                            item["page"] = page_idx + 1
                            cleaned = _clean_item(item)
                            if cleaned:
                                all_items.append(cleaned)

                        # First page metadata
                        if page_idx == 0:
                            if parsed.get("supplier"):
                                supplier = parsed["supplier"]
                            raw_date = parsed.get("date", "")
                            if raw_date:
                                doc_date = normalize_date(raw_date)
                            if parsed.get("currency"):
                                currency = parsed["currency"]
                            if parsed.get("document_type") and parsed["document_type"] != "unknown":
                                doc_type = parsed["document_type"]

                        break  # success

                    else:
                        last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"

                except httpx.ConnectError as e:
                    last_error = f"Connection error: {e}"
                except httpx.TimeoutException:
                    last_error = "Timeout"
                except Exception as e:
                    logger.warning("vision LLM retry failed (caution: may contain doc data)", exc_info=True)
                    last_error = f"Error: {e}"

            if last_error:
                warnings.append(f"Page {page_idx + 1}: {last_error}")

        except Exception as e:
            logger.exception("vision LLM outer loop failed for page %d", page_idx + 1)
            warnings.append(f"Page {page_idx + 1}: {str(e)}")

    return {
        "document_type": doc_type,
        "supplier": supplier,
        "currency": currency,
        "date": doc_date,
        "items": all_items,
        "warnings": warnings,
        "extraction_method": "vision",
    }
