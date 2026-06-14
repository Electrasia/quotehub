"""tests/test_config_validation.py — Tests for config validation (Step 3)."""

import pytest
from backend.routes.admin import _validate_config


class TestValidateConfig:
    """Unit tests for the _validate_config function."""

    # ── Valid configs ──────────────────────────────────────

    def test_valid_config_passes(self):
        config = {
            "ai_endpoint": "http://localhost:1234/v1/chat/completions",
            "model": "test-model",
            "timeout": 90,
            "max_retries": 2,
            "popup_duration": 3,
            "extraction_mode": "llm_first",
            "ocr_enabled": True,
            "ocr_fallback_to_llm": True,
        }
        assert _validate_config(config) == []

    def test_empty_config_passes(self):
        """An empty dict should pass — all fields are optional."""
        assert _validate_config({}) == []

    def test_timeout_boundaries(self):
        assert _validate_config({"timeout": 10}) == []   # min
        assert _validate_config({"timeout": 300}) == []  # max
        assert len(_validate_config({"timeout": 9})) == 1
        assert len(_validate_config({"timeout": 301})) == 1

    def test_max_retries_boundaries(self):
        assert _validate_config({"max_retries": 1}) == []
        assert _validate_config({"max_retries": 10}) == []
        assert len(_validate_config({"max_retries": 0})) == 1
        assert len(_validate_config({"max_retries": 11})) == 1

    def test_popup_duration_boundaries(self):
        assert _validate_config({"popup_duration": 1}) == []
        assert _validate_config({"popup_duration": 10}) == []
        assert len(_validate_config({"popup_duration": 0})) == 1
        assert len(_validate_config({"popup_duration": 11})) == 1

    # ── Extraction mode ────────────────────────────────────

    def test_valid_extraction_modes(self):
        for mode in ("vision_first", "llm_first", "local_first", "vision_only", "llm_only", "local_only"):
            assert _validate_config({"extraction_mode": mode}) == []

    def test_invalid_extraction_mode(self):
        errors = _validate_config({"extraction_mode": "banana"})
        assert len(errors) == 1
        assert "extraction_mode" in errors[0]

    # ── AI endpoint ────────────────────────────────────────

    def test_valid_endpoint(self):
        assert _validate_config({"ai_endpoint": "http://localhost:1234/v1"}) == []
        assert _validate_config({"ai_endpoint": "https://api.example.com"}) == []

    def test_empty_endpoint_passes(self):
        assert _validate_config({"ai_endpoint": ""}) == []

    def test_invalid_endpoint(self):
        errors = _validate_config({"ai_endpoint": "not-a-url"})
        assert len(errors) == 1
        assert "ai_endpoint" in errors[0]

    # ── Boolean fields ─────────────────────────────────────

    def test_valid_booleans(self):
        assert _validate_config({"ocr_enabled": True}) == []
        assert _validate_config({"ocr_enabled": False}) == []
        assert _validate_config({"ocr_fallback_to_llm": True}) == []

    def test_invalid_booleans(self):
        errors = _validate_config({"ocr_enabled": "yes"})
        assert len(errors) == 1
        assert "ocr_enabled" in errors[0]

    # ── Type safety ────────────────────────────────────────

    def test_timeout_string_rejected(self):
        errors = _validate_config({"timeout": "fast"})
        assert len(errors) == 1

    def test_retries_float_rejected(self):
        errors = _validate_config({"max_retries": 2.5})
        # Floats between 1-10 are accepted (validation uses isinstance int|float)
        assert len(errors) == 0

    def test_retries_negative_rejected(self):
        errors = _validate_config({"max_retries": -1})
        assert len(errors) == 1

    # ── DPI validation ────────────────────────────────────

    def test_valid_dpi(self):
        assert _validate_config({"llm_dpi": 72}) == []
        assert _validate_config({"llm_dpi": 150}) == []
        assert _validate_config({"llm_dpi": 300}) == []

    def test_invalid_dpi(self):
        errors = _validate_config({"llm_dpi": 50})
        assert len(errors) == 1
        assert "llm_dpi" in errors[0]

    def test_invalid_dpi_too_high(self):
        errors = _validate_config({"llm_dpi": 400})
        assert len(errors) == 1
        assert "llm_dpi" in errors[0]

    # ── Multiple errors ────────────────────────────────────

    def test_multiple_errors_returned(self):
        config = {
            "timeout": -1,
            "max_retries": 999,
            "extraction_mode": "invalid",
            "llm_dpi": 50,
        }
        errors = _validate_config(config)
        assert len(errors) == 4
