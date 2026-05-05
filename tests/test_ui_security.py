"""Tests for UI server security helpers and HTTP-level security headers."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from ovp_pipeline.commands.ui_server import _safe_redirect_path, create_server


class TestSafeRedirectPath:
    def test_normal_path(self):
        assert _safe_redirect_path("/search") == "/search"

    def test_path_with_query(self):
        assert _safe_redirect_path("/ops/objects?q=test") == "/ops/objects?q=test"

    def test_empty_falls_back(self):
        assert _safe_redirect_path("") == "/"

    def test_absolute_url_rejected(self):
        assert _safe_redirect_path("https://evil.com") == "/"

    def test_protocol_relative_rejected(self):
        assert _safe_redirect_path("//evil.com") == "/"

    def test_backslash_rejected(self):
        assert _safe_redirect_path("\\evil.com") == "/"

    def test_crlf_injection_rejected(self):
        assert _safe_redirect_path("/ok\r\nX-Injected: yes") == "/"

    def test_null_byte_rejected(self):
        assert _safe_redirect_path("/ok\x00evil") == "/"

    def test_relative_no_slash_rejected(self):
        assert _safe_redirect_path("evil") == "/"

    def test_custom_fallback(self):
        assert _safe_redirect_path("", fallback="/home") == "/home"

    def test_scheme_no_netloc_rejected(self):
        assert _safe_redirect_path("javascript:alert(1)") == "/"


# ---------------------------------------------------------------------------
# HTTP-level security tests (CSP headers, CSRF double-submit cookie)
# ---------------------------------------------------------------------------


def _boot_server(vault: Path):
    server = create_server(vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port, t


def _teardown(server, t):
    server.shutdown()
    server.server_close()
    t.join(timeout=5)


def _seed_minimal(vault: Path) -> None:
    """Set up a minimal vault so the server can respond to requests."""
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index

    eg = vault / "10-Knowledge" / "Evergreen"
    eg.mkdir(parents=True, exist_ok=True)
    (eg / "Stub.md").write_text(
        "---\ntitle: Stub\ntype: evergreen\ntags: [evergreen]\n---\nStub.\n",
        encoding="utf-8",
    )
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    rebuild_knowledge_index(vault)


class TestCSPHeader:
    """Content-Security-Policy header must be present on HTML and JSON responses."""

    def test_csp_on_html_page(self, temp_vault):
        _seed_minimal(temp_vault)
        server, port, t = _boot_server(temp_vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            resp.read()
            csp = resp.getheader("Content-Security-Policy", "")
            assert "default-src" in csp, "CSP header missing on HTML response"
            assert "'self'" in csp
        finally:
            _teardown(server, t)

    def test_csp_on_json_response(self, temp_vault):
        _seed_minimal(temp_vault)
        server, port, t = _boot_server(temp_vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/runtime-state")
            resp = conn.getresponse()
            resp.read()
            csp = resp.getheader("Content-Security-Policy", "")
            assert "default-src" in csp, "CSP header missing on JSON response"
        finally:
            _teardown(server, t)


class TestCSRFDoubleSubmit:
    """CSRF double-submit cookie must be set and validated on POST."""

    def test_csrf_cookie_set_on_html(self, temp_vault):
        _seed_minimal(temp_vault)
        server, port, t = _boot_server(temp_vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            resp.read()
            cookies = resp.getheader("Set-Cookie", "")
            assert "_csrf=" in cookies, "CSRF cookie not set on HTML response"
            assert "SameSite=Strict" in cookies
            assert "HttpOnly" in cookies
        finally:
            _teardown(server, t)

    def test_csrf_meta_tag_in_html(self, temp_vault):
        _seed_minimal(temp_vault)
        server, port, t = _boot_server(temp_vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            assert 'name="csrf-token"' in body, "CSRF meta tag missing in HTML"
        finally:
            _teardown(server, t)

    def test_post_without_csrf_cookie_allowed(self, temp_vault):
        """POST without a csrf cookie should still proceed (no cookie → no check)."""
        _seed_minimal(temp_vault)
        server, port, t = _boot_server(temp_vault)
        try:
            body = "limit=5"
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/api/runtime-state",
                body=body.encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp = conn.getresponse()
            resp.read()
            assert resp.status != 403, "Should not reject when no CSRF cookie present"
        finally:
            _teardown(server, t)

    def test_post_with_bad_csrf_token_rejected(self, temp_vault):
        """POST with a valid csrf cookie but mismatched form token → 403."""
        _seed_minimal(temp_vault)
        server, port, t = _boot_server(temp_vault)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            resp.read()
            cookie = resp.getheader("Set-Cookie", "")
            csrf_cookie = cookie.split(";")[0]

            body = "_csrf=wrong_token&limit=5"
            conn2 = HTTPConnection("127.0.0.1", port, timeout=5)
            conn2.request(
                "POST",
                "/api/runtime-state",
                body=body.encode(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Cookie": csrf_cookie,
                },
            )
            resp2 = conn2.getresponse()
            resp2.read()
            assert resp2.status == 403, f"Expected 403, got {resp2.status}"
        finally:
            _teardown(server, t)
