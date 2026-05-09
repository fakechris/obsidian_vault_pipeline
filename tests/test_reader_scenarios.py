"""Reader happy-path scenario tests (Phase 3 C2).

Each test validates a complete reader journey through the UI
WITHOUT operator/workbench elements being visible.
Link traversal extracts actual hrefs from HTML to verify real E2E paths.
"""
from __future__ import annotations

import json
import re
import threading
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import unquote

from ovp_pipeline.commands.ui_server import create_server
from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _seed(vault: Path) -> None:
    """Populate a minimal vault for reader scenario testing."""
    eg = vault / "10-Knowledge" / "Evergreen"
    eg.mkdir(parents=True, exist_ok=True)
    (eg / "Alpha.md").write_text(
        "---\ntitle: Alpha\ntype: evergreen\ntags: [evergreen, ai]\n---\nAlpha concept.\n",
        encoding="utf-8",
    )
    (eg / "Beta.md").write_text(
        "---\ntitle: Beta\ntype: evergreen\ntags: [evergreen, ai]\n---\nBeta links to [[Alpha]].\n",
        encoding="utf-8",
    )
    atlas = vault / "10-Knowledge" / "Atlas"
    atlas.mkdir(parents=True, exist_ok=True)
    (atlas / "MOC-AI-Research.md").write_text(
        "---\ntitle: MOC AI Research\ntype: moc\n---\n# AI Research\n- [[Alpha]]\n- [[Beta]]\n",
        encoding="utf-8",
    )
    proc = vault / "50-Inbox" / "03-Processed" / "2026-04"
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "Source Article.md").write_text(
        "---\ntitle: Source Article\nsource: https://example.com/src\n---\nRaw article text.\n",
        encoding="utf-8",
    )
    interp = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
    interp.mkdir(parents=True, exist_ok=True)
    (interp / "Source Article_深度解读.md").write_text(
        "---\ntitle: Source Article 深度解读\ntype: interpretation\nsource: https://example.com/src\n---\n"
        "Interpretation of [[Alpha]] and [[Beta]].\n",
        encoding="utf-8",
    )
    logs = vault / "60-Logs"
    logs.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "event_type": "evergreen_auto_promoted",
            "concept": "alpha",
            "source": "Source Article_深度解读.md",
            "mutation": {"target_slug": "alpha"},
        },
        {
            "event_type": "source_archived_to_processed",
            "source": "50-Inbox/02-Processing/Source Article.md",
            "archived": str(proc / "Source Article.md"),
        },
    ]
    (logs / "pipeline.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)


def _get(port: int, path: str) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp.status, resp.read().decode("utf-8")


def _get_redirect(port: int, path: str) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    resp.read()
    return resp.status, resp.getheader("Location", "")


_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']')


def _extract_hrefs(html: str, prefix: str) -> list[str]:
    """Extract all href values from *html* that start with *prefix*."""
    return [m.group(1) for m in _HREF_RE.finditer(html) if m.group(1).startswith(prefix)]


# ---------------------------------------------------------------------------
# Scenario 1: Library home → Object page → Evidence links
# ---------------------------------------------------------------------------

def test_reader_library_to_object_to_evidence(temp_vault):
    _seed(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        st, home = _get(port, "/")
        assert st == 200
        assert "Knowledge Library" in home
        # BL-050: Reader shell exposes a single cross-link to
        # Maintenance, but no full Workbench card on the home.
        assert "Open Workbench" not in home
        assert "→ Maintenance" in home

        # Reader home no longer lists typed objects; reach an object
        # via search instead so the rest of the chain still exercises.
        object_links = _extract_hrefs(home, "/search")
        assert object_links, "home page should expose a search entrypoint"

        # An object lens still has to avoid maintainer-flavored cards
        # like Next Actions.  Pull a representative object_id from the
        # seeded vault.
        first_link = "/object?id=alpha"
        st, obj = _get(port, first_link)
        assert st == 200
        assert "Next Actions" not in obj

        evidence_links = _extract_hrefs(obj, "/note?") + _extract_hrefs(obj, "/object?")
        assert evidence_links, "object page should contain evidence or related links"
        ev_st, _ = _get(port, evidence_links[0])
        assert ev_st == 200
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# Scenario 2: Search → Result → Object page
# ---------------------------------------------------------------------------

def test_reader_search_to_object(temp_vault):
    _seed(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        st, search = _get(port, "/search?q=Alpha")
        assert st == 200
        assert "Search" in search
        # Reader page may carry a single cross-link to /ops, but
        # never a deep link into the Maintainer subtree.
        assert 'href="/ops/' not in search

        result_links = _extract_hrefs(search, "/object?")
        assert result_links, "search results should contain /object? links"
        st, obj = _get(port, result_links[0])
        assert st == 200
        # Object lens may surface targeted maintainer actions
        # (e.g. "Review scoped contradictions") — reader purity is
        # enforced on home/search/map listings, not deep-dive lenses.
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# Scenario 3: Graph navigation
# ---------------------------------------------------------------------------

def test_reader_graph_navigation(temp_vault):
    """``/map`` post-AtlasGraph: per-node ``/object?`` click-through
    paths live in the ``window.OVP_GRAPH`` JSON payload consumed by
    ``/static/atlas-graph.js`` (which wires them onto the
    "Open in vault →" button in the right-detail panel).  Extract
    one path and confirm the underlying object route still loads —
    preserves the scenario intent (reader can drill from the map
    into an object page) without a JS-aware browser harness.
    """
    import json

    _seed(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        st, graph = _get(port, "/map")
        assert st == 200
        assert "Atlas" in graph

        marker = 'id="ovp-atlas-data" type="application/json">'
        start = graph.index(marker) + len(marker)
        end = graph.index("</script>", start)
        raw = (
            graph[start:end]
            .replace("\\u003c", "<")
            .replace("\\u003e", ">")
            .replace("\\u0026", "&")
        )
        data = json.loads(raw)
        obj_paths = [
            node["path"]
            for node in data["nodes"]
            if isinstance(node.get("path"), str) and node["path"].startswith("/object?")
        ]
        assert obj_paths, "/map atlas payload should carry at least one /object? path"
        st, _ = _get(port, obj_paths[0])
        assert st == 200
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# Scenario 4: Evidence traceability — object → note → back
# ---------------------------------------------------------------------------

def test_reader_evidence_traceability(temp_vault):
    _seed(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        st, obj = _get(port, "/object?id=alpha")
        assert st == 200
        assert "Alpha" in obj

        note_links = _extract_hrefs(obj, "/note?")
        assert note_links, "object page should contain at least one /note? link"
        st, note = _get(port, note_links[0])
        assert st == 200
        assert 'href="/ops/' not in note
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# Scenario 5: Reader ↔ Maintainer cross-link (BL-050 hard split)
# ---------------------------------------------------------------------------

def test_reader_to_ops_cross_link(temp_vault):
    """BL-050: ``?mode=operator`` is gone.  ``/`` always exposes a
    one-way cross-link to ``/ops`` and ``/ops`` always exposes the
    return cross-link.  Each shell renders its own nav."""
    _seed(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        st, reader = _get(port, "/")
        assert st == 200
        assert 'href="/ops"' in reader
        assert "→ Maintenance" in reader

        st, ops = _get(port, "/ops")
        assert st == 200
        assert 'href="/"' in ops
        assert "← Back to Library" in ops
        assert "OVP Truth UI" in ops or "Workbench" in ops or "Objects Indexed" in ops
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)
