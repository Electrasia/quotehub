# ─── Auth: passwords, session helpers, role dependencies ───
import os
import sqlite3
import secrets
import warnings
from pathlib import Path
from typing import Optional

# Suppress passlib/bcrypt version-check warning (harmless on local install)
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from passlib.hash import bcrypt as _bcrypt

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field

# ─── Paths ────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "quotations.db"
INIT_PASSWORD_FILE = DATA_DIR / "init_password.txt"

# ─── Session constants ────────────────────────────────────
SESSION_USER_ID = "user_id"

# ─── Password hashing (bcrypt, cost factor 12) ────────────
def hash_password(plain: str) -> str:
    return _bcrypt.using(rounds=12).hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.verify(plain, hashed)
    except (ValueError, TypeError):
        return False

# ─── DB helpers for users table ───────────────────────────
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_by_username(username: str) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def create_user(username: str, password: str, role: str,
                must_change_password: bool = False) -> int:
    pw_hash = hash_password(password)
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password) "
            "VALUES (?, ?, ?, ?)",
            (username, pw_hash, role, 1 if must_change_password else 0)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def update_user_password(user_id: int, new_password: str) -> None:
    pw_hash = hash_password(new_password)
    conn = _connect()
    try:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
        conn.commit()
    finally:
        conn.close()

def update_user_role(user_id: int, new_role: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
    finally:
        conn.close()

def soft_delete_user(user_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

def set_user_active(user_id: int, active: bool) -> None:
    """Toggle a user's active flag. Used by Edit form to activate/deactivate."""
    conn = _connect()
    try:
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
        conn.commit()
    finally:
        conn.close()

def hard_delete_user(user_id: int) -> None:
    """Permanently remove a user row. Cannot be undone."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

def count_masters() -> int:
    """Count ALL masters (active + inactive). Used for hard-delete protection."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'master'"
        ).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()

def clear_must_change_password(user_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE users SET must_change_password = 0 WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

def record_login(user_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user_id,)
        )
        conn.commit()
    finally:
        conn.close()

def count_active_masters() -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'master' AND active = 1"
        ).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()

def list_users() -> list:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, username, role, active, must_change_password, "
            "created_at, last_login FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def has_any_user() -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return (row["n"] if row else 0) > 0
    finally:
        conn.close()

# ─── Session / current user ───────────────────────────────
def get_current_user(request: Request) -> Optional[dict]:
    user_id = request.session.get(SESSION_USER_ID)
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user or not user.get("active"):
        # Stale or deleted user — clear session
        request.session.pop(SESSION_USER_ID, None)
        return None
    return user

def require_role(*allowed_roles: str):
    """FastAPI dependency factory. Returns the current user when role is allowed,
    raises 401 when not logged in, 403 when role is wrong."""
    def dependency(request: Request) -> dict:
        user = get_current_user(request)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated"
            )
        if user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return user
    return dependency

# ─── Pydantic models ──────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)
    role: str = Field(pattern="^(master|admin|user)$")

class UserUpdate(BaseModel):
    role: Optional[str] = Field(default=None, pattern="^(master|admin|user)$")
    new_password: Optional[str] = Field(default=None, min_length=1)
    active: Optional[bool] = Field(default=None)

class UserOut(BaseModel):
    id: int
    username: str
    role: str
    active: int
    must_change_password: int
    created_at: Optional[str] = None
    last_login: Optional[str] = None

# ─── Bootstrap: first-run master account ─────────────────
def _write_init_password_file(password: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INIT_PASSWORD_FILE.write_text(password)
    try:
        os.chmod(INIT_PASSWORD_FILE, 0o600)
    except OSError:
        pass  # On some FS (e.g. FAT) chmod is a no-op

def _print_init_banner(username: str, password: str) -> None:
    print("")
    print("=" * 60)
    print("  INITIAL MASTER CREDENTIALS")
    print("=" * 60)
    print(f"  Username: {username}")
    print(f"  Password: {password}")
    print(f"  Also saved in: {INIT_PASSWORD_FILE}")
    print("  The file is auto-deleted after the master changes the password.")
    print("=" * 60)
    print("")

def bootstrap_master() -> None:
    """Create the initial master account if no users exist.
    Idempotent: if the user or a previous init file exists, reuses them.
    """
    if has_any_user():
        return
    # If a previous bootstrap wrote the file but crashed before creating the user,
    # reuse the password. Otherwise generate a fresh one.
    if INIT_PASSWORD_FILE.exists():
        password = INIT_PASSWORD_FILE.read_text().strip()
    else:
        password = secrets.token_urlsafe(12)  # ~16 characters
        _write_init_password_file(password)
    create_user(
        username="master",
        password=password,
        role="master",
        must_change_password=True,
    )
    _print_init_banner("master", password)

def read_init_password() -> Optional[str]:
    if INIT_PASSWORD_FILE.exists():
        return INIT_PASSWORD_FILE.read_text().strip()
    return None

def acknowledge_init_password() -> bool:
    """Delete the init password file. Returns True if a file was removed."""
    if INIT_PASSWORD_FILE.exists():
        try:
            INIT_PASSWORD_FILE.unlink()
            return True
        except OSError:
            return False
    return False
