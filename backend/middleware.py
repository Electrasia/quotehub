"""
backend/middleware.py — Custom session cookie middleware for "Remember Me" support.

This middleware intercepts responses after SessionMiddleware and rewrites the
Set-Cookie header based on a "remember_me" flag stored in the session.

- remember_me=True:  Cookie gets Max-Age=1209600 (14 days)
- remember_me=False: No Max-Age set (session cookie, expires on browser close)
"""

import re
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# 14 days in seconds
REMEMBER_ME_MAX_AGE = 14 * 24 * 60 * 60

# Pattern to match the session cookie Set-Cookie header
COOKIE_PATTERN = re.compile(
    r'(Set-Cookie:\s*quotahub_session=[^;]+)(?:;\s*Max-Age=\d+)?([^;]*;?.*)',
    re.IGNORECASE
)


class SessionCookieMiddleware(BaseHTTPMiddleware):
    """Rewrite session cookie Max-Age based on remember_me flag in session."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Only process responses with Set-Cookie header for our session cookie
        cookie_header = response.headers.get("set-cookie", "")
        if "quotahub_session=" not in cookie_header:
            return response

        # Check remember_me flag from session (set during login)
        remember_me = request.session.get("remember_me", False)

        if remember_me:
            # Rewrite cookie to have 14-day Max-Age
            new_cookie = self._set_max_age(cookie_header, REMEMBER_ME_MAX_AGE)
            response.headers["set-cookie"] = new_cookie
        else:
            # Remove Max-Age to make it a session cookie (expires on browser close)
            new_cookie = self._remove_max_age(cookie_header)
            response.headers["set-cookie"] = new_cookie

        return response

    def _set_max_age(self, cookie_header: str, max_age: int) -> str:
        """Set or replace Max-Age in the Set-Cookie header."""
        if "Max-Age=" in cookie_header:
            # Replace existing Max-Age
            return re.sub(r'Max-Age=\d+', f'Max-Age={max_age}', cookie_header)
        else:
            # Add Max-Age before the first semicolon after the cookie value
            return cookie_header.replace(';', f'; Max-Age={max_age};', 1)

    def _remove_max_age(self, cookie_header: str) -> str:
        """Remove Max-Age from the Set-Cookie header."""
        return re.sub(r';\s*Max-Age=\d+', '', cookie_header)
