"""tests/test_auth.py — Authentication and user management endpoint tests.

Tests cover login, logout, rate limiting, session management,
password changes, user CRUD, and role-based access control.

All fixtures are defined in tests/conftest.py (Phase 1).
"""

import pytest


# ─── Login ────────────────────────────────────────────────────────────────────

class TestLogin:
    """Tests for POST /auth/login."""

    def test_valid_login_returns_200(self, seeded_db):
        """Login with correct credentials should succeed."""
        resp = seeded_db.post("/auth/login", json={
            "username": "master01",
            "password": "Mast3r!Pass12",
            "remember_me": False,
        })
        assert resp.status_code == 200

    def test_wrong_password_returns_401(self, seeded_db):
        """Login with incorrect password should fail."""
        resp = seeded_db.post("/auth/login", json={
            "username": "master01",
            "password": "wrongpass",
            "remember_me": False,
        })
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

    def test_nonexistent_user_returns_401(self, seeded_db):
        """Login with unknown username should fail."""
        resp = seeded_db.post("/auth/login", json={
            "username": "nobody",
            "password": "Irrelev4nt!Pass",
            "remember_me": False,
        })
        assert resp.status_code == 401

    def test_disabled_account_returns_403(self, seeded_db):
        """Login for a disabled account should be rejected with 403."""
        from backend.auth import set_user_active
        # user is id=3 (created third in seeded_db)
        set_user_active(3, False)

        resp = seeded_db.post("/auth/login", json={
            "username": "user",
            "password": "Us3r!Pass123",
            "remember_me": False,
        })
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Account is disabled"

    def test_remember_me_accepted(self, seeded_db):
        """Login with remember_me=true should succeed."""
        resp = seeded_db.post("/auth/login", json={
            "username": "user",
            "password": "Us3r!Pass123",
            "remember_me": True,
        })
        assert resp.status_code == 200

    def test_login_response_shape(self, seeded_db):
        """Login response should contain expected fields."""
        resp = seeded_db.post("/auth/login", json={
            "username": "admin",
            "password": "Adm1n!Pass12",
            "remember_me": False,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "id" in body
        assert "username" in body
        assert "role" in body
        assert "must_change_password" in body
        assert body["username"] == "admin"
        assert body["role"] == "admin"

    def test_session_fixation_prevention(self, seeded_db):
        """Login should clear old session data before setting new one.

        Log in as admin, then log in as master on the same client.
        If session fixation is prevented, GET /users (master-only)
        should succeed because the old admin session was cleared.
        """
        # Login as admin
        resp = seeded_db.post("/auth/login", json={
            "username": "admin",
            "password": "Adm1n!Pass12",
            "remember_me": False,
        })
        assert resp.status_code == 200

        # Login as master (clears session, sets new one)
        resp = seeded_db.post("/auth/login", json={
            "username": "master01",
            "password": "Mast3r!Pass12",
            "remember_me": False,
        })
        assert resp.status_code == 200

        # /users requires master role — should succeed with fresh session
        resp = seeded_db.get("/users")
        assert resp.status_code == 200

    def test_must_change_password_flag(self, seeded_db):
        """Login response should reflect the must_change_password flag."""
        # seeded_db creates users with must_change_password=False
        resp = seeded_db.post("/auth/login", json={
            "username": "user",
            "password": "Us3r!Pass123",
            "remember_me": False,
        })
        assert resp.status_code == 200
        assert resp.json()["must_change_password"] is False


# ─── Rate Limiting ────────────────────────────────────────────────────────────

class TestLoginRateLimiting:
    """Tests for login rate-limiting behaviour."""

    def test_rate_limit_blocks_after_5_failures(self, app_client):
        """After 5 failed logins, the 6th should be rate-limited (429)."""
        for i in range(5):
            resp = app_client.post("/auth/login", json={
                "username": f"attacker{i}",
                "password": "wrong",
                "remember_me": False,
            })
            assert resp.status_code == 401, f"Attempt {i+1} should be 401"

        resp = app_client.post("/auth/login", json={
            "username": "attacker6",
            "password": "wrong",
            "remember_me": False,
        })
        assert resp.status_code == 429
        assert "15 minutes" in resp.json()["detail"].lower()

    def test_successful_login_resets_counter(self, seeded_db):
        """A successful login should clear the failure counter."""
        # Send 3 wrong logins (counter = 3)
        for i in range(3):
            resp = seeded_db.post("/auth/login", json={
                "username": f"rando{i}",
                "password": "wrong",
                "remember_me": False,
            })
            assert resp.status_code == 401

        # Successful login (clears counter)
        resp = seeded_db.post("/auth/login", json={
            "username": "admin",
            "password": "Adm1n!Pass12",
            "remember_me": False,
        })
        assert resp.status_code == 200

        # Counter was reset — next wrong login should be 401, not 429
        resp = seeded_db.post("/auth/login", json={
            "username": "clean_slate",
            "password": "wrong",
            "remember_me": False,
        })
        assert resp.status_code == 401

    def test_disabled_account_not_counted(self, seeded_db):
        """A 403 for disabled account should NOT increment the rate-limit counter.

        Arrange: 4 wrong logins, then correct password for disabled user (403).
        If disabled is not counted, counter stays at 4 and a subsequent
        correct login for another user should succeed (not be blocked).
        """
        from backend.auth import set_user_active
        set_user_active(3, False)  # disable user (id=3)

        # 4 wrong logins
        for i in range(4):
            resp = seeded_db.post("/auth/login", json={
                "username": f"fail{i}",
                "password": "bad",
                "remember_me": False,
            })
            assert resp.status_code == 401

        # Correct password for disabled account → 403, counter NOT incremented
        resp = seeded_db.post("/auth/login", json={
            "username": "user",
            "password": "Us3r!Pass123",
            "remember_me": False,
        })
        assert resp.status_code == 403

        # Counter is still at 4 → admin login should succeed (not blocked)
        resp = seeded_db.post("/auth/login", json={
            "username": "admin",
            "password": "Adm1n!Pass12",
            "remember_me": False,
        })
        assert resp.status_code == 200
        # If disabled was counted (counter=5), this would return 429.


# ─── Logout ───────────────────────────────────────────────────────────────────

class TestLogout:
    """Tests for POST /auth/logout."""

    def test_authenticated_logout(self, master_client):
        """Logout should clear the session."""
        resp = master_client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"status": "logged out"}

        # Session is gone — /me should return 401
        resp = master_client.get("/auth/me")
        assert resp.status_code == 401

    def test_unauthenticated_logout(self, app_client):
        """Logout when not logged in should still return 200."""
        resp = app_client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"status": "logged out"}


# ─── Get Current User ─────────────────────────────────────────────────────────

class TestGetMe:
    """Tests for GET /auth/me."""

    def test_authenticated_get_me(self, master_client):
        """Authenticated user should get their info."""
        resp = master_client.get("/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "master01"
        assert body["role"] == "master"
        assert "id" in body
        assert "must_change_password" in body

    def test_unauthenticated_get_me(self, app_client):
        """Unauthenticated request should return 401."""
        resp = app_client.get("/auth/me")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Not authenticated"

    def test_get_me_must_change_password_flag(self, master_client):
        """GET /me should reflect the must_change_password flag."""
        resp = master_client.get("/auth/me")
        assert resp.status_code == 200
        # master01 was created with must_change_password=False
        assert resp.json()["must_change_password"] is False


# ─── Change Password ──────────────────────────────────────────────────────────

class TestChangePassword:
    """Tests for POST /auth/change-password."""

    def test_change_password_returns_200(self, master_client):
        """Changing password with valid data should succeed."""
        resp = master_client.post("/auth/change-password", json={
            "old_password": "Mast3r!Pass12",
            "new_password": "S3cur3!Pass99",
        })
        assert resp.status_code == 200
        assert resp.json() == {"status": "password changed"}

    def test_unauthenticated_change_password(self, app_client):
        """Changing password without auth should return 401."""
        resp = app_client.post("/auth/change-password", json={
            "old_password": "x",
            "new_password": "V4lid!Pass123",
        })
        assert resp.status_code == 401

    def test_login_with_new_password(self, master_client):
        """After changing password, login with the new password should work."""
        master_client.post("/auth/change-password", json={
            "old_password": "Mast3r!Pass12",
            "new_password": "N3w!Strong99",
        })
        master_client.post("/auth/logout")

        resp = master_client.post("/auth/login", json={
            "username": "master01",
            "password": "N3w!Strong99",
            "remember_me": False,
        })
        assert resp.status_code == 200

    def test_change_password_weak_rejected(self, master_client):
        """Changing to a weak password should return 422."""
        resp = master_client.post("/auth/change-password", json={
            "old_password": "Mast3r!Pass12",
            "new_password": "short",
        })
        assert resp.status_code == 422

    def test_login_with_old_password_fails(self, master_client):
        """After changing password, login with the old password should fail."""
        master_client.post("/auth/change-password", json={
            "old_password": "Mast3r!Pass12",
            "new_password": "N3w!Strong99",
        })
        master_client.post("/auth/logout")

        resp = master_client.post("/auth/login", json={
            "username": "master01",
            "password": "Mast3r!Pass12",
            "remember_me": False,
        })
        assert resp.status_code == 401

    def test_change_password_clears_must_change_password(self, seeded_db):
        """Changing password should clear the must_change_password flag."""
        from backend.auth import create_user

        # Create a user with must_change_password=True
        create_user("newbie", "Init!Strong99", "user", must_change_password=True)

        # Login — flag should be True
        resp = seeded_db.post("/auth/login", json={
            "username": "newbie",
            "password": "Init!Strong99",
            "remember_me": False,
        })
        assert resp.status_code == 200
        assert resp.json()["must_change_password"] is True

        # Change password
        seeded_db.post("/auth/change-password", json={
            "old_password": "Init!Strong99",
            "new_password": "N3w!Strong99",
        })

        # GET /me should now show must_change_password=False
        resp = seeded_db.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["must_change_password"] is False


# ─── User CRUD (master only) ──────────────────────────────────────────────────

class TestUserCRUD:
    """Tests for /users endpoints (master role required)."""

    def test_list_users(self, master_client):
        """GET /users should return all users."""
        resp = master_client.get("/users")
        assert resp.status_code == 200
        users = resp.json()
        assert len(users) >= 3
        usernames = [u["username"] for u in users]
        assert "master01" in usernames
        assert "admin" in usernames
        assert "user" in usernames

    def test_create_user(self, master_client):
        """POST /users with valid data should create a user."""
        resp = master_client.post("/users", json={
            "username": "newuser",
            "password": "N3w!Strong99",
            "role": "user",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "newuser"
        assert body["role"] == "user"
        assert "id" in body

    def test_create_weak_password_rejected(self, master_client):
        """POST /users with a weak password should return 422."""
        resp = master_client.post("/users", json={
            "username": "weakpwuser",
            "password": "short",
            "role": "user",
        })
        assert resp.status_code == 422

    def test_create_duplicate_username(self, master_client):
        """POST /users with existing username should return 400."""
        resp = master_client.post("/users", json={
            "username": "admin",
            "password": "Irrelev4nt!Pass",
            "role": "user",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Username already exists"

    def test_update_user_role(self, master_client):
        """PATCH /users/{id} should update the user's role."""
        # user is id=3 in seeded_db
        resp = master_client.patch("/users/3", json={
            "role": "admin",
        })
        assert resp.status_code == 200

        # Verify via list
        resp = master_client.get("/users")
        users = {u["id"]: u for u in resp.json()}
        assert users[3]["role"] == "admin"

    def test_update_nonexistent_user(self, master_client):
        """PATCH /users/9999 should return 404."""
        resp = master_client.patch("/users/9999", json={
            "role": "admin",
        })
        assert resp.status_code == 404

    def test_delete_user(self, master_client):
        """DELETE /users/{id} should remove the user."""
        # Create a user to delete
        resp = master_client.post("/users", json={
            "username": "todelete",
            "password": "T3st!Str0ng99",
            "role": "user",
        })
        user_id = resp.json()["id"]

        # Delete it
        resp = master_client.delete(f"/users/{user_id}")
        assert resp.status_code == 200

        # Verify gone from list
        resp = master_client.get("/users")
        usernames = [u["username"] for u in resp.json()]
        assert "todelete" not in usernames

    def test_delete_nonexistent_user(self, master_client):
        """DELETE /users/9999 should return 404."""
        resp = master_client.delete("/users/9999")
        assert resp.status_code == 404

    def test_created_user_appears_in_list(self, master_client):
        """After creating a user, it should appear in the user list."""
        resp = master_client.post("/users", json={
            "username": "listcheck",
            "password": "T3st!Str0ng99",
            "role": "admin",
        })
        assert resp.status_code == 200

        resp = master_client.get("/users")
        usernames = [u["username"] for u in resp.json()]
        assert "listcheck" in usernames


# ─── Role-Based Access Control ────────────────────────────────────────────────

class TestUserCRUDRoles:
    """Tests that /users endpoints enforce require_role('master')."""

    def test_admin_cannot_list_users(self, admin_client):
        """Admin should get 403 when listing users."""
        resp = admin_client.get("/users")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Insufficient permissions"

    def test_user_cannot_list_users(self, user_client):
        """Regular user should get 403 when listing users."""
        resp = user_client.get("/users")
        assert resp.status_code == 403

    def test_unauthenticated_cannot_list_users(self, app_client):
        """Unauthenticated request should get 401 when listing users."""
        resp = app_client.get("/users")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Not authenticated"

    def test_admin_cannot_create_users(self, admin_client):
        """Admin should get 403 when creating a user."""
        resp = admin_client.post("/users", json={
            "username": "shouldfail",
            "password": "T3st!Str0ng99",
            "role": "user",
        })
        assert resp.status_code == 403

    def test_user_cannot_update_role(self, user_client):
        """Regular user should get 403 when updating a role."""
        resp = user_client.patch("/users/1", json={
            "role": "user",
        })
        assert resp.status_code == 403

    def test_user_cannot_delete_users(self, user_client):
        """Regular user should get 403 when deleting a user."""
        resp = user_client.delete("/users/1")
        assert resp.status_code == 403
