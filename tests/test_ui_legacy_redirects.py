"""Tests for BL-050 legacy maintainer-path redirects in ``ui_server``.

When the maintainer dashboard moved under ``/ops/`` the old
top-level paths (``/candidates``, ``/contradictions``, …) became
permanent redirects.  Two contracts have to hold:

1. **GET** legacy paths → ``301 Moved Permanently`` with
   ``Location: /ops/<same>?<query>``.  Browser bookmarks and
   inline ``href`` strings rely on this.

2. **POST** legacy paths → ``308 Permanent Redirect`` (NOT 301).
   308 is the only redirect status that obliges the client to
   reissue the request with the same method + body.  301 would
   silently downgrade an action POST to a GET and the form
   submission would vanish.

A regression that swaps 308→301 on the POST path is exactly the
kind of bug type-checking and unit tests miss but breaks
production form submissions on day one.

Plus the BL-051 ``/atlas/curated`` → ``/topics`` redirect, which
shares the same handler shape.
"""

from __future__ import annotations

import threading
from http.client import HTTPConnection

import pytest

from ovp_pipeline.commands.ui_server import (
    _LEGACY_MAINTAINER_PATHS,
    create_server,
)


# Generous request budget — these tests run against an in-process
# HTTP server, so anything more than a few hundred ms is a hang.
_REQUEST_TIMEOUT_SECONDS = 5


# A subset of the 30+ legacy paths — enough to prove the table is
# scanned, not all of them (the full sweep would just multiply
# server-startup cost without adding signal).  Pick representatives
# from each functional area: candidate review (browser-driven GETs),
# action mutations (POST forms), and the read-only signals tab.
_GET_SAMPLE = (
    "/candidates",
    "/contradictions",
    "/signals",
    "/pulse",
    "/clusters",
    "/objects",
)
_POST_SAMPLE = (
    "/actions/run-next",
    "/actions/dismiss",
    "/actions/enqueue",
    "/contradictions/resolve",
    "/summaries/rebuild",
)


# ---------------------------------------------------------------------------
# Helpers — start a server, fire one request, return status + Location.
# ---------------------------------------------------------------------------


def _start_server(temp_vault):
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


def _stop_server(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _request_no_follow(
    temp_vault, *, method: str, path: str, body: str = "",
) -> tuple[int, str | None]:
    """Issue a single request without following redirects.  Returns
    ``(status, location_header)``.  ``location_header`` is ``None``
    for non-redirect responses.

    POST handler note: ``BaseHTTPRequestHandler`` sends the redirect
    response and returns *without* reading the request body.  When
    the server closes the connection the client may still be
    flushing body bytes, raising ``ConnectionResetError`` from
    ``response.read()``.  That's harmless — the status line and
    headers have already arrived — so we suppress the post-status
    drain failure and only assert on what we already have.
    """
    server, thread, port = _start_server(temp_vault)
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=_REQUEST_TIMEOUT_SECONDS)
        encoded = body.encode("utf-8") if body else b""
        if method == "POST":
            conn.request(
                "POST", path, body=encoded,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Content-Length": str(len(encoded)),
                    "Connection": "close",
                },
            )
        else:
            conn.request(method, path, headers={"Connection": "close"})
        response = conn.getresponse()
        location = response.getheader("Location")
        try:
            response.read()
        except (ConnectionResetError, OSError):
            # Server closed before draining body; status is already
            # captured.  Don't propagate — that's a transport
            # detail, not a test failure.
            pass
        return response.status, location
    finally:
        _stop_server(server, thread)


# ---------------------------------------------------------------------------
# GET legacy paths — 301 to /ops/<same>
# ---------------------------------------------------------------------------


class TestLegacyGetRedirects:
    @pytest.mark.parametrize("legacy_path", _GET_SAMPLE)
    def test_get_legacy_path_redirects_with_301(self, temp_vault, legacy_path):
        status, location = _request_no_follow(
            temp_vault, method="GET", path=legacy_path,
        )
        assert status == 301, (
            f"GET {legacy_path}: expected 301 Moved Permanently, "
            f"got {status}.  Browser bookmark migration relies on "
            "the permanent-redirect signal — a 302 would not let "
            "the browser update its history entry."
        )
        assert location == "/ops" + legacy_path

    def test_get_redirect_preserves_query_string(self, temp_vault):
        status, location = _request_no_follow(
            temp_vault, method="GET",
            path="/candidates?status=pending&page=2",
        )
        assert status == 301
        # Query verbatim — no re-encoding, no parameter reordering.
        assert location == "/ops/candidates?status=pending&page=2"

    def test_get_non_legacy_path_does_not_redirect(self, temp_vault):
        # Negative control: a path NOT in _LEGACY_MAINTAINER_PATHS
        # must not get the 301 treatment.  The path /ops/foo (already
        # under the new prefix) is the natural target.
        status, location = _request_no_follow(
            temp_vault, method="GET", path="/ops/this-path-does-not-exist",
        )
        # Either 404 or a real handler response — anything but 301
        # to ourselves.  Asserting "not 301" is enough; we don't care
        # which 4xx the dispatcher chose.
        assert status != 301
        assert location is None or not location.startswith("/ops/ops/")


# ---------------------------------------------------------------------------
# POST legacy paths — 308 (not 301) to preserve method + body
# ---------------------------------------------------------------------------


class TestLegacyPostRedirects:
    @pytest.mark.parametrize("legacy_path", _POST_SAMPLE)
    def test_post_legacy_path_redirects_with_308(self, temp_vault, legacy_path):
        # Body content is irrelevant to the redirect decision; what
        # matters is the status code.  Pre-fix bug shape: a refactor
        # downgrades the response from 308 → 301, which silently
        # converts the client's POST into a GET and drops the body.
        status, location = _request_no_follow(
            temp_vault, method="POST", path=legacy_path,
            body="action=test&_csrf=ignored",
        )
        assert status == 308, (
            f"POST {legacy_path}: expected 308 Permanent Redirect, "
            f"got {status}.  301 would force the client to reissue "
            "the request as GET and the form body would silently "
            "vanish — that is the exact regression this test pins."
        )
        assert location == "/ops" + legacy_path

    def test_post_redirect_preserves_query_string(self, temp_vault):
        status, location = _request_no_follow(
            temp_vault, method="POST",
            path="/actions/retry?run_id=abc",
            body="payload=true",
        )
        assert status == 308
        assert location == "/ops/actions/retry?run_id=abc"


# ---------------------------------------------------------------------------
# /atlas/curated  →  /topics  (BL-051, separate handler but same contract)
# ---------------------------------------------------------------------------


class TestAtlasCuratedRedirect:
    def test_html_path_redirects_to_topics(self, temp_vault):
        status, location = _request_no_follow(
            temp_vault, method="GET", path="/atlas/curated",
        )
        assert status == 301
        assert location == "/topics"

    def test_api_path_redirects_to_api_topics(self, temp_vault):
        status, location = _request_no_follow(
            temp_vault, method="GET", path="/api/atlas/curated",
        )
        assert status == 301
        assert location == "/api/topics"

    def test_redirect_preserves_query_string(self, temp_vault):
        status, location = _request_no_follow(
            temp_vault, method="GET",
            path="/atlas/curated?top_n=10&pack=research-tech",
        )
        assert status == 301
        assert location == "/topics?top_n=10&pack=research-tech"


# ---------------------------------------------------------------------------
# Sanity: every entry in _LEGACY_MAINTAINER_PATHS gets the right status.
# This is the "cheap full-sweep" that catches a typo in the table without
# spinning up 30 servers — we hit each path once with a single server.
# ---------------------------------------------------------------------------


class TestLegacyTableFullSweep:
    def test_every_legacy_path_is_covered_by_redirects(self, temp_vault):
        # Reuses one server for the whole sweep — far cheaper than
        # parametrize per path which restarts the server every time.
        server, thread, port = _start_server(temp_vault)
        try:
            for legacy_path in sorted(_LEGACY_MAINTAINER_PATHS):
                # Context-manage the connection so an assertion failure
                # mid-loop still releases the socket on the way out.
                conn = HTTPConnection(
                    "127.0.0.1", port, timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                try:
                    conn.request("GET", legacy_path)
                    response = conn.getresponse()
                    location = response.getheader("Location")
                    response.read()
                except (ConnectionResetError, OSError):
                    # See _request_no_follow's note: BaseHTTPRequestHandler
                    # closes the socket before the body fully drains.
                    pass
                finally:
                    conn.close()
                assert response.status == 301, (
                    f"GET {legacy_path}: expected 301, got "
                    f"{response.status}.  Either the path is missing "
                    "from the redirect handler or the table entry "
                    "is stale — keep both in sync."
                )
                # Also pin the destination so a future refactor can't
                # land /candidates → /ops/elsewhere without a failing
                # test.  The contract is dead-simple: prefix /ops, keep
                # the rest of the path verbatim.
                assert location == "/ops" + legacy_path, (
                    f"GET {legacy_path}: expected redirect to "
                    f"/ops{legacy_path}, got Location={location!r}"
                )
        finally:
            _stop_server(server, thread)
