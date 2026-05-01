"""Tests for UI server security helpers."""

from __future__ import annotations

from ovp_pipeline.commands.ui_server import _safe_redirect_path


class TestSafeRedirectPath:
    def test_normal_path(self):
        assert _safe_redirect_path("/search") == "/search"

    def test_path_with_query(self):
        assert _safe_redirect_path("/objects?q=test") == "/objects?q=test"

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
