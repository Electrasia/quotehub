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
    extraction_mode: str = Field("local_first", description="llm_first | local_first | llm_only | local_only")
    ocr_enabled: bool = Field(True, description="Run OCR on scanned PDFs")


@router.post("/extract", dependencies=[Depends(require_role("master"))])
async def debug_extract(req: DebugExtractRequest):
    """Extract items from a file using the specified extraction mode."""
    from ..parser import parse_file_with_ocr
    from ..extraction import extract_items_async
    
    filepath = Path(req.filepath)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Parse file
    parse_result = await parse_file_with_ocr(str(filepath))
    
    # Extract items using the router
    result = await extract_items_async(
        parse_result,
        mode=req.extraction_mode,
    )
    
    return {
        "filename": filepath.name,
        "extraction_method": result.extraction_method,
        "items": result.items,
        "supplier": result.supplier,
        "date": result.date,
        "currency": result.currency,
        "document_type": result.document_type,
        "warnings": result.warnings,
        "llm_warnings": result.llm_warnings,
    }
