"""tests/test_search.py — Search endpoint tests.

Tests cover FTS search, document type filtering, authentication,
response shape, and the limited flag.

The search endpoint is at GET /search (admin router has no prefix).
Requires role user, admin, or master.
"""

import pytest


@pytest.fixture
def user_with_quotations(seed_quotations):
    """Authenticated as 'user' against a DB with 3 seeded quotations.

    seed_quotations inserts Acme Corp (INVOICE), Beta Inc (QUOTATION),
    and Gamma Ltd (ESTIMATE) into the temp DB.
    """
    resp = seed_quotations.post("/auth/login", json={
        "username": "user",
        "password": "Us3r!Pass123",
        "remember_me": False,
    })
    assert resp.status_code == 200
    return seed_quotations


class TestSearchAuth:
    """Tests for search endpoint authentication."""

    def test_requires_authentication(self, app_client):
        """Unauthenticated requests should return 401."""
        resp = app_client.get("/search")
        assert resp.status_code == 401


class TestSearchBasic:
    """Basic search behaviour: empty query, response shape, limited flag."""

    def test_empty_query_returns_all(self, user_with_quotations):
        """With no query, all quotations should be returned (up to 10)."""
        resp = user_with_quotations.get("/search")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 3
        assert body["limited"] is False  # 3 < 10, so not limited

    def test_response_shape(self, user_with_quotations):
        """Each result should contain the expected fields."""
        resp = user_with_quotations.get("/search")
        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert "id" in result
        assert "filename" in result
        assert "supplier" in result
        assert "items" in result
        assert "document_type" in result
        # items should be parsed as a list (not a raw JSON string)
        assert isinstance(result["items"], list)

    def test_items_intact_without_search(self, user_with_quotations):
        """Without a search query, items should not be post-filtered."""
        resp = user_with_quotations.get("/search")
        body = resp.json()
        # Acme Corp has 2 items
        acme = [r for r in body["results"] if r["supplier"] == "Acme Corp"]
        assert len(acme) == 1
        assert len(acme[0]["items"]) == 2

    def test_search_no_results(self, user_with_quotations):
        """A query matching nothing should return an empty result set."""
        resp = user_with_quotations.get("/search", params={"q": "ZZTopNonexistent"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []


class TestSearchByField:
    """Search by supplier name, model, and partial word matching."""

    def test_search_by_supplier(self, user_with_quotations):
        """Searching by supplier name should find matching quotations."""
        resp = user_with_quotations.get("/search", params={"q": "Acme"})
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["supplier"] == "Acme Corp"

    def test_search_by_model(self, user_with_quotations):
        """Searching by model name should find matching quotations."""
        resp = user_with_quotations.get("/search", params={"q": "Gadget"})
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["supplier"] == "Beta Inc"

    def test_search_partial_word(self, user_with_quotations):
        """FTS prefix matching should find partial word matches."""
        resp = user_with_quotations.get("/search", params={"q": "Gad"})
        body = resp.json()
        # "Gad" should match "Gadget X" via FTS5 prefix query (Gad*)
        assert len(body["results"]) >= 1
        assert body["results"][0]["supplier"] == "Beta Inc"


class TestDocumentTypeFilter:
    """Filtering by document_type parameter."""

    def test_filter_by_type(self, user_with_quotations):
        """Filtering by a specific document type should return only matches."""
        resp = user_with_quotations.get("/search", params={"document_type": "invoice"})
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["document_type"] == "INVOICE"

    def test_filter_all_types(self, user_with_quotations):
        """document_type=ALL should return all quotations (no filter)."""
        resp = user_with_quotations.get("/search", params={"document_type": "ALL"})
        body = resp.json()
        assert len(body["results"]) == 3

    def test_filter_no_results(self, user_with_quotations):
        """Filtering by a type that doesn't exist should return empty."""
        resp = user_with_quotations.get(
            "/search", params={"document_type": "purchase_order"}
        )
        body = resp.json()
        assert body["results"] == []

    def test_search_plus_filter(self, user_with_quotations):
        """Combining search query with document type filter should work."""
        resp = user_with_quotations.get(
            "/search", params={"q": "Widget", "document_type": "invoice"}
        )
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["supplier"] == "Acme Corp"


class TestFtsRebuild:
    """FTS index rebuild: verify the index survives a manual rebuild."""

    def test_fts_rebuild_preserves_search(self, seed_quotations):
        """After INSERT INTO quotations_fts(quotations_fts) VALUES('rebuild'),
        FTS search should still return expected results."""
        from backend.db import get_db

        with get_db() as db:
            db.execute("INSERT INTO quotations_fts(quotations_fts) VALUES('rebuild')")

        # Log in as 'user' (created by seeded_db) and search
        resp = seed_quotations.post("/auth/login", json={
            "username": "user",
            "password": "Us3r!Pass123",
            "remember_me": False,
        })
        assert resp.status_code == 200

        # Search for a term that exists
        resp = seed_quotations.get("/search", params={"q": "Acme"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["supplier"] == "Acme Corp"

        # Search for a term that should be empty
        resp = seed_quotations.get("/search", params={"q": "ZZTopNonexistent"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []
