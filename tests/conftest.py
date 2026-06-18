"""tests/conftest.py — Shared fixtures for QuoteHub tests."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config.json and patch CONFIG_PATH to point to it."""
    config_data = {
        "ai_endpoint": "http://localhost:1234/v1/chat/completions",
        "model": "test-model",
        "timeout": 90,
        "max_retries": 2,
        "external_url": "",
        "popup_duration": 3,
        "extraction_enabled": True,
        "ocr_enabled": True,
        "ocr_fallback_to_llm": True,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config_data, indent=2))
    return config_path


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database path."""
    return tmp_path / "data" / "quotations.db"


@pytest.fixture
def app_client(tmp_config, tmp_path):
    """Create a TestClient with patched config and database paths.

    Patches:
    - backend.utils.CONFIG_PATH → temp config
    - backend.db.DB_PATH → temp database
    - backend.main data directories → temp directories
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "temp").mkdir(exist_ok=True)
    (data_dir / "archive").mkdir(exist_ok=True)
    (data_dir / "images").mkdir(exist_ok=True)

    with patch("backend.utils.CONFIG_PATH", tmp_config), \
         patch("backend.db.DB_PATH", data_dir / "quotations.db"), \
         patch("backend.auth.DATA_DIR", data_dir), \
         patch("backend.main.DATA_DIR", data_dir), \
         patch("backend.main.ARCHIVE_DIR", data_dir / "archive"), \
         patch("backend.main.IMAGES_DIR", data_dir / "images"), \
         patch("backend.main.UPLOAD_DIR", data_dir / "temp"), \
         patch("backend.main.SECRET_KEY_PATH", data_dir / "secret.key"):

        from backend.main import app
        from backend.db import init_db
        # init_db() runs at module level in backend/main.py, but since
        # Python caches module imports, it only executes once.  Each
        # test gets a fresh temp DB via the DB_PATH patch above, so
        # we call init_db() explicitly here to create the schema.
        init_db()
        client = TestClient(app)
        yield client


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear the in-memory rate limiter state before each test.

    Prevents cross-test leakage of _FAILED_LOGINS that would cause
    spurious 429 responses in rate-limit tests.
    """
    from backend.routes.auth import _FAILED_LOGINS
    _FAILED_LOGINS.clear()


@pytest.fixture
def seeded_db(app_client):
    """Create known test users in the temp database.

    Creates three users — master01, admin, user — each with a known
    password. Returns the same TestClient as app_client so callers
    can make requests.

    Depends on app_client so the temp-DB path patches are active.
    """
    from backend.auth import create_user

    create_user("master01", "masterpass", "master", must_change_password=False)
    create_user("admin", "adminpass", "admin", must_change_password=False)
    create_user("user", "userpass", "user", must_change_password=False)
    return app_client


@pytest.fixture
def master_client(seeded_db):
    """TestClient authenticated as master01 (master role)."""
    resp = seeded_db.post("/auth/login", json={
        "username": "master01",
        "password": "masterpass",
        "remember_me": False,
    })
    assert resp.status_code == 200, f"master login failed: {resp.json()}"
    return seeded_db


@pytest.fixture
def admin_client(seeded_db):
    """TestClient authenticated as admin (admin role)."""
    resp = seeded_db.post("/auth/login", json={
        "username": "admin",
        "password": "adminpass",
        "remember_me": False,
    })
    assert resp.status_code == 200, f"admin login failed: {resp.json()}"
    return seeded_db


@pytest.fixture
def user_client(seeded_db):
    """TestClient authenticated as user (user role)."""
    resp = seeded_db.post("/auth/login", json={
        "username": "user",
        "password": "userpass",
        "remember_me": False,
    })
    assert resp.status_code == 200, f"user login failed: {resp.json()}"
    return seeded_db


@pytest.fixture
def seed_quotations(seeded_db):
    """Insert sample quotations into the temp database.

    Inserts 3 quotations with realistic item data for search,
    export, and admin tests.
    """
    from backend.db import get_db

    samples = [
        {
            "filename": "acme_quote.pdf",
            "supplier": "Acme Corp",
            "quotation_date": "2025-01-15",
            "currency": "USD",
            "document_type": "INVOICE",
            "items": json.dumps([
                {"model": "Widget A", "brand": "Acme",
                 "description": "Standard widget", "unit_price": 10.0, "quantity": 5},
                {"model": "Widget B", "brand": "Acme",
                 "description": "Premium widget", "unit_price": 25.0, "quantity": 3},
            ]),
            "extraction_method": "local",
        },
        {
            "filename": "beta_quote.xlsx",
            "supplier": "Beta Inc",
            "quotation_date": "2025-02-20",
            "currency": "EUR",
            "document_type": "QUOTATION",
            "items": json.dumps([
                {"model": "Gadget X", "brand": "Beta",
                 "description": "Gadget with extra features", "unit_price": 99.0, "quantity": 10},
            ]),
            "extraction_method": "ai_text",
        },
        {
            "filename": "gamma_estimate.pdf",
            "supplier": "Gamma Ltd",
            "quotation_date": "2025-03-10",
            "currency": "GBP",
            "document_type": "ESTIMATE",
            "items": json.dumps([
                {"model": "Service A", "brand": "Gamma",
                 "description": "Consulting service", "unit_price": 150.0, "quantity": 1},
                {"model": "Service B", "brand": "Gamma",
                 "description": "Support service", "unit_price": 75.0, "quantity": 4},
                {"model": "Part C", "brand": "Gamma",
                 "description": "Replacement part", "unit_price": 12.5, "quantity": 20},
            ]),
            "extraction_method": "vision",
        },
    ]

    with get_db() as db:
        for q in samples:
            db.execute(
                """INSERT INTO quotations
                   (filename, supplier, quotation_date, currency,
                    items, document_type, extraction_method)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (q["filename"], q["supplier"], q["quotation_date"],
                 q["currency"], q["items"], q["document_type"],
                 q["extraction_method"]),
            )
    return seeded_db


# ─── Export/import test constants ─────────────────────────

TEST_EXPORT_PASSWORD = "Str0ng!P@ss42"


@pytest.fixture
def export_password_set(master_client, monkeypatch):
    """Set export password to TEST_EXPORT_PASSWORD with low KDF iterations.

    Patches PBKDF2_ITERATIONS to 1 so crypto operations in subsequent
    test calls are fast. Returns the authenticated TestClient.
    """
    monkeypatch.setattr("backend.export_import.PBKDF2_ITERATIONS", 1)
    resp = master_client.post("/export-password", json={
        "new_password": TEST_EXPORT_PASSWORD,
    })
    assert resp.status_code == 200, f"set export password failed: {resp.json()}"
    return master_client


@pytest.fixture
def with_archive_files(seeded_db):
    """Create archive files matching seed_quotations filenames in ARCHIVE_DIR.

    Creates 3 files that match the filenames used by the seed_quotations
    fixture (acme_quote.pdf, beta_quote.xlsx, gamma_estimate.pdf) plus an
    extra orphan file for export tests.
    """
    from backend.main import ARCHIVE_DIR
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    filenames = ["acme_quote.pdf", "beta_quote.xlsx", "gamma_estimate.pdf"]
    for name in filenames:
        (ARCHIVE_DIR / name).write_text(f"fake content for {name}")
    # Extra orphan file (not referenced by any record) for export warnings
    (ARCHIVE_DIR / "orphan_old.pdf").write_text("old backup content")
    return seeded_db
