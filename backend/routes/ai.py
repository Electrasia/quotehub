"""
backend/routes/ai.py — AI server connection endpoints.

This module handles:
    - Testing connection to AI server (LM Studio)
    - Checking AI server status
"""

import httpx
from fastapi import APIRouter, Depends

from ..auth import require_role
from ..utils import load_config

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/connect", dependencies=[Depends(require_role("admin", "master"))])
async def connect_ai():
    """Test connection to AI server."""
    cfg = load_config()
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    
    if not endpoint:
        return {"status": "failed", "error": "AI endpoint not configured"}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Try to list models or send a simple request
            response = await client.get(f"{endpoint}/v1/models")
            if response.status_code == 200:
                return {"status": "connected", "endpoint": endpoint, "model": model}
            else:
                return {"status": "failed", "error": f"HTTP {response.status_code}"}
    except httpx.ConnectError:
        return {"status": "failed", "error": "Cannot connect to AI server"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


@router.get("/status", dependencies=[Depends(require_role("admin", "master"))])
async def ai_status():
    """Check AI server status."""
    cfg = load_config()
    endpoint = cfg.get("ai_endpoint", "")
    model = cfg.get("model", "")
    
    if not endpoint:
        return {"connected": False, "error": "Not configured"}
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{endpoint}/v1/models")
            if response.status_code == 200:
                return {"connected": True, "endpoint": endpoint, "model": model}
            else:
                return {"connected": False, "error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"connected": False, "error": str(e)}
