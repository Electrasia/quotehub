"""
backend/routes/auth.py — Authentication and user management endpoints.

This module handles:
    - Login/logout
    - Password changes
    - User CRUD (create, read, update, delete)
    - Session management
    - Initial password setup
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import (
    require_role, LoginRequest, ChangePasswordRequest,
    UserCreate, UserUpdate, SESSION_USER_ID,
    get_current_user, get_user_by_username, get_user_by_id,
    create_user, update_user_password, update_user_role,
    hard_delete_user, list_users,
    read_init_password, acknowledge_init_password,
    verify_password, record_login,
)

# Main auth router (login/logout/password)
router = APIRouter(prefix="/auth", tags=["auth"])

# User management router (CRUD)
users_router = APIRouter(prefix="/users", tags=["users"])

# Initial password router
init_password_router = APIRouter(prefix="/init-password", tags=["init-password"])


# ─── Authentication ────────────────────────────────────────

@router.post("/login")
async def login(req: LoginRequest, request: Request):
    """Authenticate user and create session."""
    user = get_user_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.get("active"):
        raise HTTPException(status_code=403, detail="Account is disabled")
    
    # Clear any existing session data (prevents session fixation)
    request.session.clear()
    
    # Set session data
    request.session[SESSION_USER_ID] = user["id"]
    request.session["remember_me"] = req.remember_me
    record_login(user["id"])
    
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "must_change_password": bool(user.get("must_change_password")),
    }


@router.post("/logout")
async def logout(request: Request):
    """Clear session."""
    request.session.clear()
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
    return {"id": user_id, "username": req.username, "role": req.role}


@users_router.patch("/{user_id}", dependencies=[Depends(require_role("master"))])
async def update_user(user_id: int, req: UserUpdate):
    """Update user role."""
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    update_user_role(user_id, req.role)
    return {"status": "updated"}


@users_router.delete("/{user_id}", dependencies=[Depends(require_role("master"))])
async def delete_user(user_id: int):
    """Permanently delete a user."""
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    hard_delete_user(user_id)
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
