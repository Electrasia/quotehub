"""
backend/extraction/vision.py — Vision LLM-based quotation item extractor.

Sends PDF page images directly to a Vision LLM (e.g., Qwen3-vl-4B) for
extraction. This bypasses OCR and text extraction, providing more accurate
results for scanned documents and complex table layouts.

The Vision LLM can see:
- Table structure and column positions
- Visual headers and section dividers
- Cell boundaries and formatting
- Brand logos and product images

This module is used by the extraction router when mode="vision_first".
"""
import json
import re
import base64
from pathlib import Path

import httpx


# Prompt for Vision LLM extraction
_VISION_EXTRACT_PROMPT = """Extract ALL items from this document image (quotation, purchase order, or price list).

═══ STEP 1: IDENTIFY TABLE STRUCTURE ═══

Look at the image and identify:
1. Table headers (what each column represents)
2. Column positions (left to right)
3. Row boundaries

═══ STEP 2: EXTRACT ITEMS ═══

For each row in the table:
- If the row has a MODEL/PART NUMBER and a PRICE → extract it as an item
- If the row has only a DESCRIPTION (no model, no price) → it's a category header, SKIP IT
- If the row is a TOTAL or SUBTOTAL → SKIP IT

═══ STEP 3: FIELD RULES ═══

- BRAND: Product manufacturer (e.g., Sony, QSC, Digisol) — NOT the supplier
- MODEL: Alphanumeric code (e.g., CZ7270-000, SRP-X700)
- DESCRIPTION: Full product description
- QUANTITY: Numeric only (e.g., "2", "10", "5.5")
- UNIT: pcs, set, m, lot, etc.
- PRICE: Unit price (strip currency symbols, format as X,XXX.XX)
- TOTAL: Line total (strip currency symbols, format as X,XXX.XX)

═══ OUTPUT FORMAT ═══

Return ONLY valid JSON:
{{
  "document_type": "QUO" | "PO" | "PL" | "unknown",
  "supplier": "company issuing the document",
  "currency": "ISO 4217 code",
  "date": "YYYY-MM-DD",
  "items": [
    {{
      "brand": "manufacturer",
      "model": "model/part number",
      "description": "full description",
      "quantity": "numeric string",
      "unit": "pcs, set, m, etc.",
      "unit_price": "X,XXX.XX",
      "total": "X,XXX.XX",
      "remark": "any notes"
    }}
  ]
}}

Return ONLY valid parseable JSON, no markdown, no explanation.
"""


def _render_page_to_image(pdf_path: str, page_num: int, dpi: int = 150) -> bytes:
    """Render a PDF page to JPEG image bytes.
    
    Args:
        pdf_path: Path to PDF file
        page_num: Page number (0-indexed)
        dpi: Image resolution (default: 150)
    
    Returns:
        JPEG image bytes
    """
    import fitz  # PyMuPDF
    from PIL import Image
    import io
    
    doc = fitz.open(pdf_path)
    try:
        if page_num >= len(doc):
            raise ValueError(f"Page {page_num} does not exist")
        
        page = doc[page_num]
        
        # Render at specified DPI
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix)
        
        # Convert to PIL Image for compression
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        # Compress to JPEG
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue()
    finally:
        doc.close()


async def extract_with_vision(
    pdf_path: str,
    cfg: dict = None,
    dpi: int = 150,
) -> dict:
    """Extract items from PDF using Vision LLM.
    
    Sends PDF page images directly to the Vision LLM for extraction.
    This is more accurate than text-based extraction for scanned documents.
    
    Args:
        pdf_path: Path to PDF file
        cfg: Configuration dict (loaded from config.json if None)
        dpi: Image resolution for rendering (default: 150)
    
    Returns:
        dict with keys:
            - document_type: str
            - supplier: str
            - currency: str
            - date: str
            - items: list of extracted items
            - warnings: list of warnings
            - extraction_method: "vision"
    """
    from ..utils import load_config, repair_json_quotes
    
    if cfg is None:
        cfg = load_config()
    
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    timeout = cfg.get("timeout", 90)
    max_retries = cfg.get("max_retries", 2)
    
    if not endpoint or not model:
        return {
            "document_type": "unknown",
            "supplier": "",
            "currency": "",
            "date": "",
            "items": [],
            "warnings": ["AI endpoint or model not configured"],
            "extraction_method": "vision",
        }
    
    # Get number of pages
    import fitz
    doc = fitz.open(pdf_path)
    num_pages = len(doc)
    doc.close()
    
    all_items = []
    suppliers = []
    dates = []
    currencies = []
    document_types = []
    warnings = []
    
    for page_idx in range(num_pages):
        try:
            # Render page to image
            img_bytes = _render_page_to_image(pdf_path, page_idx, dpi)
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            
            # Build message with image
            messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": _VISION_EXTRACT_PROMPT,
                    },
                ],
            }]
            
            # Call Vision LLM
            last_error = None
            for attempt in range(max_retries):
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(timeout=timeout, connect=5.0),
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
                        if "choices" not in result or not result.get("choices"):
                            last_error = "AI returned no choices"
                            continue
                        
                        msg = result["choices"][0]["message"]
                        raw = (msg.get("content") or
                               msg.get("reasoning_content") or "").strip()
                        
                        # Clean JSON from response
                        if raw.startswith("```"):
                            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                        if raw.endswith("```"):
                            raw = raw[:-3]
                        raw = raw.strip()
                        
                        # Extract JSON
                        start = raw.find("{")
                        end = raw.rfind("}") + 1
                        if start == -1:
                            last_error = "No JSON in LLM response"
                            continue
                        
                        json_str = raw[start:end] if end > start else raw[start:]
                        if end <= start:
                            json_str = json_str.rstrip(",") + "]}"
                        
                        # Parse JSON
                        parsed = None
                        for candidate in (json_str, repair_json_quotes(json_str)):
                            try:
                                parsed = json.loads(candidate)
                                break
                            except json.JSONDecodeError:
                                continue
                        
                        if parsed is None:
                            # Try suffixes
                            for suffix in ["}", "\"}]}", "}]}", "]}"]:
                                try:
                                    parsed = json.loads(json_str.rstrip(",") + suffix)
                                    break
                                except json.JSONDecodeError:
                                    continue
                        
                        if parsed is None:
                            last_error = f"Could not parse JSON: {raw[:200]}"
                            continue
                        
                        # Extract items from this page
                        page_items = parsed.get("items", [])
                        for item in page_items:
                            item["page"] = page_idx + 1
                            all_items.append(item)
                        
                        if parsed.get("supplier"):
                            suppliers.append(parsed["supplier"])
                        if parsed.get("date"):
                            dates.append(parsed["date"])
                        if parsed.get("currency"):
                            currencies.append(parsed["currency"])
                        if parsed.get("document_type") and parsed["document_type"] != "unknown":
                            document_types.append(parsed["document_type"])
                        
                        break  # Success, move to next page
                    
                    else:
                        last_error = f"AI returned HTTP {resp.status_code}: {resp.text[:200]}"
                
                except httpx.ConnectError as e:
                    last_error = f"Connection error: {e}"
                except httpx.TimeoutException:
                    last_error = "Timeout"
                except Exception as e:
                    last_error = f"Error: {e}"
            
            if last_error:
                warnings.append(f"Page {page_idx + 1}: {last_error}")
        
        except Exception as e:
            warnings.append(f"Page {page_idx + 1}: {str(e)}")
    
    # Merge metadata (first non-empty value wins)
    supplier = suppliers[0] if suppliers else ""
    shared_date = dates[0] if dates else ""
    currency = currencies[0] if currencies else ""
    document_type = document_types[0] if document_types else "unknown"
    
    return {
        "document_type": document_type,
        "supplier": supplier,
        "currency": currency,
        "date": shared_date,
        "items": all_items,
        "warnings": warnings,
        "extraction_method": "vision",
    }


def _normalize_item(item):
    """Normalize a single Vision LLM-extracted item."""
    if not isinstance(item, dict):
        return None
    
    # Handle quantity: if "3800 pcs", split to qty="3800", unit="pcs"
    qty_raw = str(item.get("quantity", "") or "").strip()
    unit_raw = str(item.get("unit", "") or "").strip()
    
    if qty_raw and not unit_raw:
        qty_match = re.match(r'^([\d,.]+)\s*([a-zA-Z]+)$', qty_raw)
        if qty_match:
            qty_raw = qty_match.group(1)
            unit_raw = qty_match.group(2)
    
    # Normalize price
    def _norm_price(s):
        if not s:
            return ""
        s = str(s)
        s = s.replace("HK$", "").replace("US$", "")
        s = s.replace("$", "").replace("€", "").replace("£", "")
        s = s.replace("¥", "").replace("￥", "")
        s = re.sub(r"\b(HKD|USD|EUR|GBP|CNY|RMB|JPY|HK|US|MOP)\b",
                    "", s, flags=re.IGNORECASE)
        s = s.replace(" ", "").replace(",", "")
        try:
            num = float(s)
            if num == int(num):
                return f"{int(num):,}"
            return f"{num:,.2f}"
        except (ValueError, TypeError):
            return ""
    
    out = {
        "brand": str(item.get("brand", "") or "").strip(),
        "model": str(item.get("model", "") or "").strip(),
        "description": str(item.get("description", "") or "").strip(),
        "quantity": qty_raw,
        "unit": unit_raw,
        "unit_price": _norm_price(item.get("unit_price", "")),
        "total": _norm_price(item.get("total", "")),
        "remark": str(item.get("remark", "") or "").strip(),
    }
    
    if not out["model"] and not out["description"]:
        return None
    if not out["unit_price"] and not out["total"]:
        return None
    if not out["model"] and out["description"]:
        out["model"] = out["description"]
    
    return out


def _validate_items(items):
    """Validate and filter extracted items."""
    validated = []
    for item in items:
        normalized = _normalize_item(item)
        if normalized:
            # Check quantity is numeric
            qty = normalized.get("quantity", "")
            if qty:
                clean_qty = qty.replace(",", "").replace(" ", "").strip()
                if not re.match(r'^\d+(\.\d+)?$', clean_qty):
                    # Move invalid quantity to remark
                    remark = normalized.get("remark", "")
                    normalized["remark"] = f"{remark} | Qty was: {qty}".strip(" |")
                    normalized["quantity"] = ""
            
            validated.append(normalized)
    return validated
