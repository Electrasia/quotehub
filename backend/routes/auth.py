"""
backend/routes/auth.py — Authentication and user management endpoints.

This module handles:
    - Login/logout
    - Password changes
    - User CRUD (create, read, update, delete)
    - Session management
    - Initial password setup
"""

import logging
import time
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import (
    require_role, LoginRequest, ChangePasswordRequest,
    UserCreate, UserUpdate, SESSION_USER_ID,
    get_current_user, get_user_by_username, get_user_by_id,
    create_user, update_user_password, update_user_role,
    hard_delete_user, list_users,
    read_init_password, acknowledge_init_password,
    clear_must_change_password, verify_password, record_login,
)

logger = logging.getLogger(__name__)

# Main auth router (login/logout/password)
router = APIRouter(prefix="/auth", tags=["auth"])

# User management router (CRUD)
users_router = APIRouter(prefix="/users", tags=["users"])

# Initial password router
init_password_router = APIRouter(prefix="/init-password", tags=["init-password"])


# ─── Login Rate Limiter ────────────────────────────────────────────
# In-memory IP-based. Lost on container restart — acceptable.
# Without a reverse proxy in Docker, all users share one IP
# (Docker gateway), making this effectively a global bucket.
# Deploy behind nginx/Caddy with X-Forwarded-For for per-IP limiting.
_FAILED_LOGINS: dict[str, list[float]] = {}
_MAX_FAILED_ATTEMPTS = 5
_WINDOW_SECONDS = 900       # 15 min sliding window
_BLOCK_SECONDS = 900         # 15 min block after hitting limit


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request, respecting proxy headers."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"


def _check_rate_limit(ip: str) -> bool:
    """Check if IP is rate-limited. Returns True if allowed, False if blocked."""
    now = time.time()
    timestamps = _FAILED_LOGINS.get(ip, [])

    # Prune: keep only entries within the sliding window.
    # Includes a clock-jump guard: reject timestamps > 5 min in the future.
    cutoff = now - _WINDOW_SECONDS
    valid_until = now + 300  # 5 min clock-drift tolerance
    timestamps = [t for t in timestamps if cutoff < t < valid_until]

    if len(timestamps) >= _MAX_FAILED_ATTEMPTS:
        oldest = timestamps[0]
        block_end = oldest + _BLOCK_SECONDS
        if now < block_end:
            return False  # still blocked
        # Block expired — reset
        timestamps = []

    _FAILED_LOGINS[ip] = timestamps
    return True


# ─── Authentication ────────────────────────────────────────

@router.post("/login")
async def login(req: LoginRequest, request: Request):
    """Authenticate user and create session."""
    # ── Rate limit check ──────────────────────────────
    client_ip = _get_client_ip(request)
    if not _check_rate_limit(client_ip):
        logger.warning("Login rate limit triggered", extra={
            'category': 'AUTH', 'ip': client_ip
        })
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again in 15 minutes."
        )

    user = get_user_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        _FAILED_LOGINS.setdefault(client_ip, []).append(time.time())
        logger.warning("Login failed - invalid credentials", extra={
            'category': 'AUTH',
            'user': req.username
        })
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.get("active"):
        logger.warning("Login failed - account disabled", extra={
            'category': 'AUTH',
            'user': req.username
        })
        raise HTTPException(status_code=403, detail="Account is disabled")
    
    # Clear failed login counter on success
    _FAILED_LOGINS.pop(client_ip, None)

    # Clear any existing session data (prevents session fixation)
    request.session.clear()
    
    # Set session data
    request.session[SESSION_USER_ID] = user["id"]
    request.session["remember_me"] = req.remember_me
    record_login(user["id"])
    
    logger.info("Login successful", extra={
        'category': 'AUTH',
        'user': user["username"],
        'role': user["role"],
        'remember_me': req.remember_me
    })
    
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "must_change_password": bool(user.get("must_change_password")),
    }


@router.post("/logout")
async def logout(request: Request):
    """Clear session."""
    user_id = request.session.get(SESSION_USER_ID)
    request.session.clear()
    logger.info("User logged out", extra={
        'category': 'AUTH',
        'user_id': user_id
    })
    return {"status": "logged out"}


@router.get("/me")
async def get_me(request: Request):
    """Get current user info."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "must_change_password": bool(user.get("must_change_password")),
    }


@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, request: Request):
    """Change current user's password."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    update_user_password(user["id"], req.new_password)
    clear_must_change_password(user["id"])
    logger.info("Password changed", extra={
        'category': 'AUTH',
        'user_id': user["id"]
    })
    return {"status": "password changed"}


# ─── User Management (master only) ────────────────────────

@users_router.get("", dependencies=[Depends(require_role("master"))])
async def get_users():
    """List all users."""
    return list_users()


@users_router.post("", dependencies=[Depends(require_role("master"))])
async def add_user(req: UserCreate):
    """Create a new user."""
    existing = get_user_by_username(req.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    user_id = create_user(req.username, req.password, req.role)
    logger.info("User created", extra={
        'category': 'AUTH',
        'new_username': req.username,
        'new_role': req.role
    })
    return {"id": user_id, "username": req.username, "role": req.role}


@users_router.patch("/{user_id}", dependencies=[Depends(require_role("master"))])
async def update_user(user_id: int, req: UserUpdate):
    """Update user role."""
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    update_user_role(user_id, req.role)
    logger.info("User role updated", extra={
        'category': 'AUTH',
        'target_user_id': user_id,
        'new_role': req.role
    })
    return {"status": "updated"}


@users_router.delete("/{user_id}", dependencies=[Depends(require_role("master"))])
async def delete_user(user_id: int):
    """Permanently delete a user."""
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    hard_delete_user(user_id)
    logger.info("User deleted", extra={
        'category': 'AUTH',
        'target_user_id': user_id
    })
    return {"status": "deleted"}


# ─── Initial Password ─────────────────────────────────────

@init_password_router.get("/status", dependencies=[Depends(require_role("master"))])
async def init_password_status():
    """Check if initial password has been acknowledged."""
    return {"acknowledged": not read_init_password()}


@init_password_router.post("/acknowledge", dependencies=[Depends(require_role("master"))])
async def init_password_acknowledge():
    """Acknowledge initial password (one-time use)."""
    acknowledge_init_password()
    return {"status": "acknowledged"}
