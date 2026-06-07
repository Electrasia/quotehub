"""
backend/ocr.py — OCR fallback for scanned/image-only PDFs.

When pdfplumber and PyMuPDF both return little or no text from a PDF
(typical for scanned documents or image-only PDFs), this module:

1. Renders each page as an image (via PyMuPDF, 200 DPI).
2. Runs pytesseract on each page to get text + per-page confidence.
3. If pytesseract output is low quality (avg confidence < 40 OR fewer
   than 5 digits/numbers in the whole document), falls back to a
   vision LLM call (image-mode) for better accuracy.

Returns plain text that the rest of the pipeline can consume just like
text from pdfplumber/PyMuPDF.

The threshold of "5 numbers" is a rough proxy for "this OCR found
items" — quotations always have prices/qtys which are numbers. If the
OCR found <5 numbers, it's probably noise.
"""
import io
import re
import time
from pathlib import Path
from typing import Any

# Tesseract returns per-page confidence in image_to_data's "conf" field,
# as an int from -1 (none) to 100. We treat anything < 40 as "low
# quality" and prompt the LLM fallback.
PytesseractUnavailable = (
    ImportError, ModuleNotFoundError, OSError,
)


def _is_tesseract_available() -> bool:
    """Check if tesseract binary + pytesseract are usable."""
    try:
        import pytesseract  # noqa: F401
        # Try a no-op call to confirm the binary is on PATH
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _render_page_to_pil(page) -> Any:
    """Render a PyMuPDF page to a PIL Image at 200 DPI."""
    from PIL import Image
    # 200/72 = 2.78x zoom
    zoom = 200 / 72.0
    matrix = __import__("fitz").Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return img


def ocr_page_pytesseract(page) -> dict:
    """Run tesseract on a single PyMuPDF page.

    Returns {"text": str, "confidence": float, "num_count": int}.
    confidence is the average of per-word confidence (0-100). If
    tesseract isn't available, returns text="" and confidence=0.
    """
    try:
        import pytesseract
    except Exception:
        return {"text": "", "confidence": 0.0, "num_count": 0,
                "error": "pytesseract not installed"}

    img = _render_page_to_pil(page)
    try:
        # image_to_data gives per-word confidence; we average across words
        data = pytesseract.image_to_data(
            img, lang="eng+chi_sim", output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractError as e:
        return {"text": "", "confidence": 0.0, "num_count": 0,
                "error": f"tesseract error: {e}"}
    except Exception as e:
        return {"text": "", "confidence": 0.0, "num_count": 0,
                "error": f"OCR error: {e}"}

    words = []
    confidences = []
    for word, conf in zip(data.get("text", []), data.get("conf", [])):
        if word and conf is not None and int(conf) >= 0:
            words.append(word)
            confidences.append(int(conf))
    text = " ".join(words)
    avg_conf = (sum(confidences) / len(confidences)) if confidences else 0.0
    # Count numbers (digits in the text). Quotations always have prices/qtys.
    num_count = len(re.findall(r"\d+", text))
    return {
        "text": text,
        "confidence": round(avg_conf, 1),
        "num_count": num_count,
    }


def ocr_pdf_pytesseract(pdf_path: str) -> dict:
    """Run pytesseract on every page of a PDF.

    Returns:
        {
            "available": bool,        # tesseract usable?
            "text": str,              # combined text from all pages
            "pages": [                # per-page details
                {"page": 1, "text": "...", "confidence": 78.3, "num_count": 42},
                ...
            ],
            "avg_confidence": float,
            "total_num_count": int,
            "time_ms": int,
            "error": str | None,
        }
    """
    t0 = time.time()
    out = {
        "available": _is_tesseract_available(),
        "text": "",
        "pages": [],
        "avg_confidence": 0.0,
        "total_num_count": 0,
        "time_ms": 0,
    }
    if not out["available"]:
        out["error"] = "pytesseract or tesseract binary not available"
        out["time_ms"] = int((time.time() - t0) * 1000)
        return out

    try:
        import fitz
    except ImportError as e:
        out["error"] = f"PyMuPDF not installed: {e}"
        out["time_ms"] = int((time.time() - t0) * 1000)
        return out

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        out["error"] = f"Could not open PDF: {e}"
        out["time_ms"] = int((time.time() - t0) * 1000)
        return out

    try:
        text_parts = []
        confidences = []
        for page_idx, page in enumerate(doc, start=1):
            res = ocr_page_pytesseract(page)
            out["pages"].append({
                "page": page_idx,
                "text": res.get("text", ""),
                "confidence": res.get("confidence", 0.0),
                "num_count": res.get("num_count", 0),
            })
            if res.get("text"):
                text_parts.append(res["text"])
            if res.get("confidence", 0) > 0:
                confidences.append(res["confidence"])
            out["total_num_count"] += res.get("num_count", 0)
            if res.get("error"):
                # Surface the first per-page error at top level too
                if not out.get("error"):
                    out["error"] = f"page {page_idx}: {res['error']}"
        out["text"] = "\n\n".join(text_parts)
        out["avg_confidence"] = round(
            sum(confidences) / len(confidences), 1,
        ) if confidences else 0.0
    finally:
        doc.close()
    out["time_ms"] = int((time.time() - t0) * 1000)
    return out


async def ocr_pdf_via_llm(pdf_path: str) -> dict:
    """Fallback: render each page and ask a vision LLM to read the text.

    Used when pytesseract is unavailable or returns low-quality output.
    This is slower and depends on the AI server being reachable, but
    handles fonts/layouts that defeat tesseract.

    Returns:
        {
            "available": bool,
            "text": str,
            "pages": [{"page": 1, "text": "..."}],
            "time_ms": int,
            "error": str | None,
        }
    """
    # Lazy imports to avoid circular import (main.py imports parser).
    from .main import load_config
    import httpx

    t0 = time.time()
    out = {
        "available": True,
        "text": "",
        "pages": [],
        "time_ms": 0,
    }

    cfg = load_config()
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    timeout = cfg.get("timeout", 180)
    max_retries = cfg.get("max_retries", 1)  # OCR fallback is best-effort
    external_url = cfg.get("external_url", "").rstrip("/")

    if not endpoint or not model:
        out["available"] = False
        out["error"] = "AI endpoint or model not configured"
        out["time_ms"] = int((time.time() - t0) * 1000)
        return out

    try:
        import fitz
        from PIL import Image
    except ImportError as e:
        out["available"] = False
        out["error"] = f"PyMuPDF or PIL not installed: {e}"
        out["time_ms"] = int((time.time() - t0) * 1000)
        return out

    # Render all pages to JPEGs (compressed, max 1280 wide)
    import base64
    import tempfile
    image_paths = []
    try:
        doc = fitz.open(pdf_path)
        try:
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                img = _render_page_to_pil(page)
                # Compress to JPEG
                if img.width > 1280:
                    ratio = 1280 / img.width
                    img = img.resize(
                        (1280, int(img.height * ratio)),
                        Image.LANCZOS,
                    )
                if img.mode == "RGBA":
                    img = img.convert("RGB")
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".jpg", delete=False,
                )
                img.save(tmp.name, "JPEG", quality=80, optimize=True)
                image_paths.append(tmp.name)
        finally:
            doc.close()
    except Exception as e:
        out["error"] = f"Could not render PDF: {e}"
        out["time_ms"] = int((time.time() - t0) * 1000)
        # Clean up any partial renders
        for p in image_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        return out

    if not image_paths:
        out["error"] = "PDF has no pages to OCR"
        out["time_ms"] = int((time.time() - t0) * 1000)
        return out

    # Build prompt — ask for text per page, NOT structured JSON
    prompt = (
        "This is page {p} of a quotation document. "
        "Read ALL the text on this page exactly as written. "
        "Preserve table structure by using pipe characters ' | ' between "
        "columns and newlines between rows. Return ONLY the text — no "
        "explanation, no markdown formatting."
    )

    try:
        last_error = None
        for attempt in range(max_retries):
            try:
                content_parts = []
                for i, img_path in enumerate(image_paths):
                    if external_url and "localhost" not in external_url \
                            and "127.0.0.1" not in external_url:
                        # Use a relative URL (we don't have the same path
                        # structure as main.py:compress_image uses; fall
                        # through to base64 for safety).
                        pass
                    with open(img_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                        },
                    })
                    content_parts.append({
                        "type": "text",
                        "text": prompt.format(p=i + 1),
                    })
                messages = [{"role": "user", "content": content_parts}]

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
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()
                    out["text"] = raw
                    # No per-page split available; treat as single chunk
                    out["pages"] = [{"page": 1, "text": raw}]
                    out["time_ms"] = int((time.time() - t0) * 1000)
                    return out
                else:
                    last_error = (
                        f"AI returned HTTP {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
            except httpx.ConnectError as e:
                last_error = f"Connection error: {e}"
            except httpx.TimeoutException:
                last_error = "Timeout"
            except Exception as e:
                last_error = f"Error: {e}"
        out["error"] = last_error or "All retries exhausted"
    finally:
        for p in image_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
    out["time_ms"] = int((time.time() - t0) * 1000)
    return out


def should_fallback_to_llm(tesseract_result: dict) -> bool:
    """Decide whether the tesseract output is too low quality to use
    and we should ask the vision LLM instead.

    Triggers LLM fallback when:
    - tesseract is not available, OR
    - avg confidence < 40, OR
    - fewer than 5 numbers in the whole document (no prices/qtys
      detected → likely gibberish).
    """
    if not tesseract_result.get("available"):
        return True
    if tesseract_result.get("error"):
        return True
    if tesseract_result.get("avg_confidence", 100) < 40:
        return True
    if tesseract_result.get("total_num_count", 0) < 5:
        return True
    return False


async def ocr_pdf(pdf_path: str, use_llm_fallback: bool = True) -> dict:
    """High-level OCR: try tesseract first, fall back to vision LLM
    if tesseract output is low quality.

    Returns a dict with at least:
        {
            "source": "tesseract" | "llm" | "none",
            "text": str,
            "time_ms": int,
            "error": str | None,
        }
    Plus detail fields (`avg_confidence`, `total_num_count`, `pages`)
    when source is tesseract.
    """
    tess = ocr_pdf_pytesseract(pdf_path)
    if not should_fallback_to_llm(tess):
        return {
            "source": "tesseract",
            "text": tess.get("text", ""),
            "time_ms": tess.get("time_ms", 0),
            "avg_confidence": tess.get("avg_confidence", 0.0),
            "total_num_count": tess.get("total_num_count", 0),
            "pages": tess.get("pages", []),
            "error": None,
        }
    if not use_llm_fallback:
        # Caller chose not to use LLM. Return whatever tesseract gave us.
        return {
            "source": "tesseract",
            "text": tess.get("text", ""),
            "time_ms": tess.get("time_ms", 0),
            "avg_confidence": tess.get("avg_confidence", 0.0),
            "total_num_count": tess.get("total_num_count", 0),
            "pages": tess.get("pages", []),
            "error": tess.get("error") or "Tesseract quality below threshold; LLM fallback disabled",
        }
    # Fall back to vision LLM
    llm = await ocr_pdf_via_llm(pdf_path)
    return {
        "source": "llm",
        "text": llm.get("text", ""),
        "time_ms": llm.get("time_ms", 0),
        "pages": llm.get("pages", []),
        "error": llm.get("error"),
    }
