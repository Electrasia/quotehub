"""
backend/normalize.py — LLM fallback for the rules-based extractor.

When the local extractor returns 0 items, this module calls the AI server
(text mode, not image mode) to get a second chance at extraction.

Text mode is much cheaper than the image-mode VLM call we use elsewhere
in the app: a typical quotation PDF is <3,000 chars of text, which fits
in the 4,096-token context window of small models like qwen3-vl-4b.

The prompt is the same shape as the image-mode prompt in main.py:call_ai,
so the LLM is told to return items in our exact JSON format.

The output is normalized to the same shape as the rules-based extractor
(items = [{brand, model, description, quantity, unit, unit_price, total, remark}])
so callers can drop in the LLM result without further processing.
"""
import json
import re
import base64
from pathlib import Path

import httpx


# A compact prompt that fits in the small context window of qwen3-vl-4b.
# Mirrors the rules in main.py:call_ai (the image-mode prompt) so the
# LLM is told to output the same JSON shape.
_TEXT_NORMALIZE_PROMPT = """Extract items from this quotation document. Return ONLY valid JSON.

STRICT ITEM FILTERING:
- A valid item MUST have a model/part number AND a numeric unit price.
- Ignore rows that are category headers, subtotals, totals, or "Optional" notes.

MODEL RULES (CRITICAL):
- Extract ONLY ONE model per item. If multiple, use ONLY the primary; ignore optional alternatives.
- "change to X" → use ONLY X. "include in ..." → IGNORE the entire row.

ROW STRUCTURE:
- Each item = ONE table row. DO NOT merge or mix values from adjacent rows.

DESCRIPTION RULES:
- Copy full description exactly. Merge multiline into ONE field.

FIELD NORMALIZATION:
- PRICE: numeric only, comma as thousand separator, period as decimal, always 2 decimals. e.g. 1157.50, 1500.00. NO currency symbols.
- CURRENCY: ISO 4217 code (USD, EUR, HKD, GBP, JPY, CNY, MOP, etc.). Infer from context.
- DATE: YYYY-MM-DD. e.g. 20/1/2026 → 2026-01-20.

DOCUMENT TYPE: "QUO" (quotation), "PO" (purchase order), "PL" (price list), or "unknown".

Return this exact structure:
{{
  "document_type": "QUO" | "PO" | "PL" | "unknown",
  "supplier": "full company name issuing this quotation",
  "currency": "ISO 4217 code",
  "date": "YYYY-MM-DD",
  "items": [
    {{
      "brand": "product brand/manufacturer",
      "model": "product model or part number",
      "description": "full description",
      "quantity": "numeric string",
      "unit": "pc, m, set, etc.",
      "unit_price": "formatted: X,XXX.XX",
      "total": "formatted: X,XXX.XX",
      "remark": "any extra notes"
    }}
  ]
}}

Return ONLY valid parseable JSON, no markdown, no explanation.

Text:
{text}
"""


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
    out = {
        "brand": str(item.get("brand", "") or "").strip(),
        "model": str(item.get("model", "") or "").strip(),
        "description": str(item.get("description", "") or "").strip(),
        "quantity": str(item.get("quantity", "") or "").strip(),
        "unit": str(item.get("unit", "") or "").strip(),
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
    # Lazy imports to avoid circular import (main.py imports this module).
    from .main import repair_json_quotes, load_config

    if cfg is None:
        cfg = load_config()
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    timeout = cfg.get("timeout", 90)
    max_retries = cfg.get("max_retries", 2)

    if not endpoint or not model:
        return {}, "AI endpoint or model not configured"

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
                    # Normalize
                    items = []
                    for it in (parsed.get("items") or []):
                        n = _normalize_item(it)
                        if n:
                            items.append(n)
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
