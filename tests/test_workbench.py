"""Phase 37 — tests for the Workbench shell + the new fragment routes."""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

import pytest

from ovp_pipeline.commands.ui_server import (
    _fragment_from_page,
    _render_workbench_page,
    create_server,
)
from ovp_pipeline.knowledge_index import rebuild_knowledge_index


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# Pure renderer tests (no server needed)
# ---------------------------------------------------------------------------


def test_fragment_from_page_strips_layout_chrome() -> None:
    """``_fragment_from_page`` must remove ``_layout``'s outer chrome and
    return only the body content. We build a synthetic page that matches the
    template and assert the inner body survives but the html/main/shell
    wrappers do not."""
    page = (
        '<!doctype html><html lang="en">'
        "<head><title>x</title></head>"
        "<body><main>"
        '<div class="shell">'
        '<div class="shell-head"><nav></nav></div>'
        '<div class="shell-body">'
        "<h1>INNER</h1><p>real content</p>"
        "</div>"
        "</div>"
        "</main></body></html>"
    )
    fragment = _fragment_from_page(page)
    assert "<h1>INNER</h1>" in fragment
    assert "<p>real content</p>" in fragment
    assert "<!doctype" not in fragment
    assert "<main>" not in fragment
    assert 'class="shell"' not in fragment


def test_fragment_from_page_falls_back_when_markers_missing() -> None:
    """Defensive: if the page doesn't contain the expected markers, return
    the original string rather than raising."""
    page = "<p>just a fragment already</p>"
    assert _fragment_from_page(page) == page


def test_render_workbench_page_includes_all_four_iframes() -> None:
    html = _render_workbench_page(object_id="alpha", requested_pack="research-tech")
    assert "id='pane-cand'" in html
    assert "id='pane-obj'" in html
    assert "id='pane-brief'" in html
    assert "id='pane-act'" in html
    assert "id='pane-pulse'" in html


def test_render_workbench_page_threads_object_id_into_object_pane() -> None:
    html = _render_workbench_page(object_id="alpha", requested_pack="research-tech")
    assert "/object/fragment?id=alpha" in html
    assert "alpha" in html  # mentioned in the header strip too


def test_render_workbench_page_falls_back_when_no_object_selected() -> None:
    html = _render_workbench_page(object_id="", requested_pack="research-tech")
    # With no object selected, the object pane's iframe src must NOT include
    # an `id=` query — the JS bridge mentions `/object/fragment` as a string
    # literal in the selectObject() builder, which is fine.
    assert "id='pane-obj' src='/object/fragment" not in html
    assert "id='pane-obj' src='/objects" in html


def test_render_workbench_page_threads_pack_into_each_pane() -> None:
    html = _render_workbench_page(object_id="", requested_pack="research-tech")
    assert "/candidates/fragment?pack=research-tech" in html
    assert "/actions/fragment?pack=research-tech" in html
    assert "/briefing/fragment?pack=research-tech" in html


# ---------------------------------------------------------------------------
# Live HTTP fragment + workbench tests
# ---------------------------------------------------------------------------


def _seed_minimal_truth_store(vault: Path) -> None:
    """Smallest seed that keeps the four payload builders happy."""
    alpha = vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)


@pytest.fixture
def running_server(temp_vault: Path):
    _seed_minimal_truth_store(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _fetch(server, path: str) -> str:
    host, port = server.server_address
    with _NO_PROXY_OPENER.open(f"http://{host}:{port}{path}", timeout=5) as resp:
        assert resp.status == 200
        return resp.read().decode("utf-8")


def test_workbench_route_returns_html_with_iframes(running_server) -> None:
    body = _fetch(running_server, "/workbench?object_id=alpha")
    assert "<title>Workbench</title>" in body
    assert "/object/fragment?id=alpha" in body
    assert "/candidates/fragment" in body
    assert "/actions/fragment" in body
    assert "/briefing/fragment" in body
    assert "/pulse/fragment" in body


def test_briefing_fragment_omits_full_page_chrome(running_server) -> None:
    fragment = _fetch(running_server, "/briefing/fragment?pack=default-knowledge")
    page = _fetch(running_server, "/briefing?pack=default-knowledge")
    # The fragment must be strictly shorter than the full page (chrome stripped).
    assert len(fragment) < len(page)
    # Fragment must not contain the outer html chrome.
    assert "<!doctype" not in fragment.lower()
    assert "<main>" not in fragment
    # But the page-specific content marker (e.g. shell nav) should be gone.
    assert "<nav>" not in fragment
    # Some recognisable page heading still survives.
    assert "Signal" in fragment or "Briefing" in fragment or "section" in fragment


def test_actions_fragment_omits_full_page_chrome(running_server) -> None:
    fragment = _fetch(running_server, "/actions/fragment?pack=default-knowledge")
    page = _fetch(running_server, "/actions?pack=default-knowledge")
    assert len(fragment) < len(page)
    assert "<!doctype" not in fragment.lower()


def test_object_fragment_omits_full_page_chrome(running_server) -> None:
    fragment = _fetch(running_server, "/object/fragment?id=alpha")
    page = _fetch(running_server, "/object?id=alpha")
    assert len(fragment) < len(page)
    assert "<!doctype" not in fragment.lower()
    # The object's title should still be present in the fragment.
    assert "Alpha" in fragment


def test_workbench_navbar_link_present(running_server) -> None:
    home = _fetch(running_server, "/")
    assert 'href="/workbench"' in home
