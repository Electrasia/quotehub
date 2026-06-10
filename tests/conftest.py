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
        "ocr_enabled": True,
        "ocr_fallback_to_llm": True,
        "extraction_mode": "llm_first",
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
        client = TestClient(app)
        yield client
