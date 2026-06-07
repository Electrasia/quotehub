"""
backend/routes/__init__.py — Route modules for QuoteHub.

This package contains all API route modules:
    - auth: Authentication and user management
    - files: File upload, processing, and management
    - ai: AI server connection
    - admin: Configuration and system administration
    - debug: Debug and inspection endpoints
"""

from .auth import router as auth_router, users_router, init_password_router
from .files import router as files_router
from .ai import router as ai_router
from .admin import router as admin_router
from .debug import router as debug_router

__all__ = [
    "auth_router",
    "users_router",
    "init_password_router",
    "files_router",
    "ai_router",
    "admin_router",
    "debug_router",
]
