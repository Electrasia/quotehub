"""
backend/routes/debug.py — Debug and inspection endpoints.

This module handles:
    - Debug file listing
    - PDF parser comparison
    - Extraction testing
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_role
from ..utils import load_config

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/files", dependencies=[Depends(require_role("master"))])
async def debug_files():
    """List files available for debugging."""
    from ..main import uploaded_files, ARCHIVE_DIR
    
    files = []
    
    # Add uploaded files
    for i, entry in enumerate(uploaded_files):
        files.append({
            "source": "upload",
            "index": i,
            "filename": entry["filename"],
            "filepath": entry.get("filepath", ""),
        })
    
    # Add archived files
    if ARCHIVE_DIR.exists():
        for pdf_file in sorted(ARCHIVE_DIR.glob("*.pdf")):
            files.append({
                "source": "archive",
                "filename": pdf_file.name,
                "filepath": str(pdf_file),
            })
    
    return {"files": files}


class DebugParseRequest(BaseModel):
    filepath: str


@router.post("/parse", dependencies=[Depends(require_role("master"))])
async def debug_parse(req: DebugParseRequest):
    """Parse a file and return raw parser output."""
    from ..parser import parse_file_with_ocr
    
    filepath = Path(req.filepath)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    result = await parse_file_with_ocr(str(filepath))
    
    # Simplify output for debugging
    output = {
        "filename": filepath.name,
        "num_pages": result.get("num_pages", 0),
        "parsers": {}
    }
    
    for parser_name, parser_data in result.get("parsers", {}).items():
        if "pages" in parser_data:
            pages = []
            for page in parser_data["pages"]:
                page_info = {
                    "page": page.get("page", 0),
                    "text_length": len(page.get("text", "")),
                    "tables_count": len(page.get("tables", [])),
                }
                # Add first 500 chars of text for preview
                text = page.get("text", "")
                page_info["text_preview"] = text[:500] + "..." if len(text) > 500 else text
                pages.append(page_info)
            output["parsers"][parser_name] = {"pages": pages}
    
    return output


class DebugExtractRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    filepath: str
    model_source: str = Field("auto", description="auto | model | part_no")
    use_llm_fallback: bool = Field(False, description="Use LLM if local returns 0 items")
    ocr_enabled: bool = Field(True, description="Run OCR on scanned PDFs")


@router.post("/extract", dependencies=[Depends(require_role("master"))])
async def debug_extract(req: DebugExtractRequest):
    """Extract items from a file."""
    from ..parser import parse_file_with_ocr
    from ..extract import extract_items
    from ..normalize import normalize_pages_with_llm
    
    filepath = Path(req.filepath)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Parse file
    parse_result = await parse_file_with_ocr(str(filepath))
    
    # Extract items locally
    result = extract_items(parse_result)
    all_items = result.get("items", [])
    extraction_method = "local"
    
    # LLM fallback if enabled and local returned 0 items
    if req.use_llm_fallback and not all_items:
        pages = parse_result.get("parsers", {}).get("pdfplumber", {}).get("pages", [])
        pages_text = [p.get("text", "") for p in pages]
        
        llm_result = await normalize_pages_with_llm(pages_text)
        if llm_result and llm_result.get("items"):
            all_items = llm_result["items"]
            extraction_method = "llm_fallback"
            if llm_result.get("supplier"):
                result["supplier"] = llm_result["supplier"]
            if llm_result.get("date"):
                result["date"] = llm_result["date"]
            if llm_result.get("currency"):
                result["currency"] = llm_result["currency"]
    
    return {
        "filename": filepath.name,
        "extraction_method": extraction_method,
        "items": all_items,
        "supplier": result.get("supplier", ""),
        "date": result.get("date", ""),
        "currency": result.get("currency", ""),
        "document_type": result.get("document_type", "unknown"),
        "warnings": result.get("extraction_warnings", []),
    }
