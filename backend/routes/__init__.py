"""
backend/routes/__init__.py — Route modules for QuoteHub.

This package contains all API route modules:
    - auth: Authentication and user management
    - files: File upload, processing, and management
    - ai: AI server connection
    - admin: Configuration and system administration
    - suppliers: Supplier management (Phase 2)
"""

from .auth import router as auth_router, users_router, init_password_router
from .files import router as files_router
from .ai import router as ai_router
from .admin import router as admin_router
from .export_import import router as export_import_router
from .auto_backup import router as auto_backup_router
from .suppliers import router as suppliers_router
from .suppliers import brands_router, product_types_router

__all__ = [
    "auth_router",
    "users_router",
    "init_password_router",
    "files_router",
    "ai_router",
    "admin_router",
    "export_import_router",
    "auto_backup_router",
    "suppliers_router",
    "brands_router",
    "product_types_router",
]
