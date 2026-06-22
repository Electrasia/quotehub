"""tests/test_admin.py — Admin endpoint tests.

Covers config, version, cleanup preview/execute/stats, brand suggestion,
duplicate check, archive serving, and logs.

All fixtures are from conftest.py — no production code changes.
"""

import pytest


# ─── Local fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def user_with_quotations(seed_quotations):
    """Authenticated as 'user' on a DB with 3 seeded quotations."""
    resp = seed_quotations.post("/auth/login", json={
        "username": "user", "password": "Us3r!Pass123", "remember_me": False,
    })
    assert resp.status_code == 200
    return seed_quotations


@pytest.fixture
def master_with_quotations(seed_quotations):
    """Authenticated as 'master01' on a DB with 3 seeded quotations."""
    resp = seed_quotations.post("/auth/login", json={
        "username": "master01", "password": "Mast3r!Pass12", "remember_me": False,
    })
    assert resp.status_code == 200
    return seed_quotations


# ─── Config ──────────────────────────────────────────────────────────────────

class TestConfig:
    """GET and POST /config."""

    def test_get_config_as_admin(self, admin_client):
        """Admin should be able to read configuration."""
        resp = admin_client.get("/config")
        assert resp.status_code == 200
        body = resp.json()
        assert "ai_endpoint" in body
        assert "timeout" in body

    def test_get_config_denied_for_user(self, user_client):
        """Regular user should be denied (admin/master only)."""
        resp = user_client.get("/config")
        assert resp.status_code == 403

    def test_update_config_as_master(self, master_client):
        """Master should be able to update configuration."""
        resp = master_client.post("/config", json={"timeout": 30})
        assert resp.status_code == 200
        assert resp.json() == {"status": "saved"}

    def test_update_config_denied_for_user(self, user_client):
        """Regular user should be denied (master only)."""
        resp = user_client.post("/config", json={"timeout": 30})
        assert resp.status_code == 403

    def test_update_config_invalid_data(self, master_client):
        """Invalid config values should return 422 with error details."""
        resp = master_client.post("/config", json={"timeout": 5})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "errors" in detail
        assert any("timeout" in e for e in detail["errors"])


# ─── Version ─────────────────────────────────────────────────────────────────

class TestVersion:
    """GET /version."""

    def test_version(self, app_client):
        """Version endpoint should return version and commit info."""
        resp = app_client.get("/version")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body
        assert "commit" in body


# ─── Cleanup ─────────────────────────────────────────────────────────────────

class TestCleanupPreview:
    """POST /cleanup/preview."""

    def test_preview_with_data(self, master_with_quotations):
        """Preview should report all old entries (seed dates precede cutoff)."""
        resp = master_with_quotations.post("/cleanup/preview", json={"months": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert body["entries"] == 3
        assert body["files"] == 0
        assert "cutoff_date" in body

    def test_preview_requires_master(self, user_client):
        """Non-master should be denied."""
        resp = user_client.post("/cleanup/preview", json={"months": 3})
        assert resp.status_code == 403


class TestCleanupExecute:
    """POST /cleanup/execute."""

    def test_execute_deletes_entries(self, master_with_quotations):
        """Execute should delete old entries and stats should reflect the change."""
        resp = master_with_quotations.post("/cleanup/execute", json={"months": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["entries_deleted"] == 3

        # Verify via stats
        resp = master_with_quotations.get("/cleanup/stats")
        assert resp.json()["total_entries"] == 0

    def test_execute_requires_master(self, user_client):
        """Non-master should be denied."""
        resp = user_client.post("/cleanup/execute", json={"months": 3})
        assert resp.status_code == 403


class TestCleanupStats:
    """GET /cleanup/stats."""

    def test_stats_with_data(self, master_with_quotations):
        """Stats should reflect the seeded quotations."""
        resp = master_with_quotations.get("/cleanup/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_entries"] == 3
        assert len(body["by_type"]) == 3
        assert body["oldest_date"] == "2025-01-15"
        assert body["newest_date"] == "2025-03-10"
        assert body["pdf_files"] == 0

    def test_stats_requires_master(self, user_client):
        """Non-master should be denied."""
        resp = user_client.get("/cleanup/stats")
        assert resp.status_code == 403


# ─── Brand Suggestion ────────────────────────────────────────────────────────

class TestBrandSuggestion:
    """GET /items/by-model."""

    def test_brand_suggestion_found(self, user_with_quotations):
        """Known model should return the associated brand."""
        resp = user_with_quotations.get("/items/by-model", params={"model": "Widget A"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["brand"] == "Acme"
        assert body["count"] >= 1

    def test_brand_suggestion_not_found(self, user_with_quotations):
        """Unknown model should return brand=None and count=0."""
        resp = user_with_quotations.get(
            "/items/by-model", params={"model": "Nonexistent"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["brand"] is None
        assert body["count"] == 0


# ─── Duplicate Check ─────────────────────────────────────────────────────────

class TestDuplicateCheck:
    """GET /check-duplicate."""

    def test_duplicate_found(self, user_with_quotations):
        """Existing filename should return in_database=true."""
        resp = user_with_quotations.get(
            "/check-duplicate", params={"filename": "acme_quote.pdf"}
        )
        assert resp.status_code == 200
        assert resp.json()["in_database"] is True

    def test_duplicate_not_found(self, user_with_quotations):
        """Non-existing filename should return in_database=false."""
        resp = user_with_quotations.get(
            "/check-duplicate", params={"filename": "nobody.pdf"}
        )
        assert resp.status_code == 200
        assert resp.json()["in_database"] is False


# ─── Archive Serving ─────────────────────────────────────────────────────────

class TestArchive:
    """GET /archive/{filename}."""

    def test_archive_not_found(self, user_client):
        """Non-existing file returns 404."""
        resp = user_client.get("/archive/nonexistent.pdf")
        assert resp.status_code == 404

    def test_archive_requires_auth(self, app_client):
        """Unauthenticated requests return 401."""
        resp = app_client.get("/archive/file.pdf")
        assert resp.status_code == 401


# ─── Logs ────────────────────────────────────────────────────────────────────

class TestLogs:
    """GET /logs."""

    def test_get_logs(self, admin_client):
        """Admin should be able to retrieve logs."""
        resp = admin_client.get("/logs")
        assert resp.status_code == 200
        body = resp.json()
        assert "logs" in body
        assert isinstance(body["logs"], list)

    def test_get_logs_error_level(self, admin_client):
        """Filtering by error level should return a list."""
        resp = admin_client.get("/logs", params={"level": "errors"})
        assert resp.status_code == 200
        body = resp.json()
        assert "logs" in body
        assert isinstance(body["logs"], list)

    def test_get_logs_requires_admin(self, user_client):
        """Regular user should be denied (admin/master only)."""
        resp = user_client.get("/logs")
        assert resp.status_code == 403
