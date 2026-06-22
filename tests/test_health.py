"""tests/test_health.py — Health check endpoint tests."""
import pytest


class TestHealth:
    """Tests for the GET /health endpoint."""

    def test_health_returns_ok(self, app_client):
        """Health endpoint should return 200 with status ok."""
        resp = app_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "ok"}
