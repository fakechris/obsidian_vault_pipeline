"""Reader happy-path scenario tests (Phase 3 C2).

Each test validates a complete reader journey through the UI
WITHOUT operator/workbench elements being visible.
"""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

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
        assert 'href="/ops"' not in home
        assert "Open Workbench" not in home
        assert 'href="/object?id=alpha' in home

        st, obj = _get(port, "/object?id=alpha")
        assert st == 200
        assert "Alpha" in obj
        assert 'href="/ops' not in obj
        assert "Next Actions" not in obj

        assert "/note?" in obj or "/object?" in obj
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
        assert 'href="/ops' not in search

        st, obj = _get(port, "/object?id=alpha")
        assert st == 200
        assert "Alpha" in obj
        assert 'href="/ops' not in obj
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# Scenario 3: Graph navigation
# ---------------------------------------------------------------------------

def test_reader_graph_navigation(temp_vault):
    _seed(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        st, graph = _get(port, "/map")
        assert st == 200
        assert "Knowledge" in graph
        assert 'href="/ops' not in graph

        st, obj = _get(port, "/object?id=alpha")
        assert st == 200
        assert "Alpha" in obj
        assert 'href="/ops' not in obj
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

        note_path = "50-Inbox/03-Processed/2026-04/Source%20Article.md"
        st, note = _get(port, f"/note?path={note_path}")
        assert st == 200
        assert "Source Article" in note
        assert 'href="/ops' not in note
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# Scenario 5: Operator mode toggle — reader ↔ operator
# ---------------------------------------------------------------------------

def test_reader_operator_mode_toggle(temp_vault):
    _seed(temp_vault)
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        st, reader = _get(port, "/")
        assert st == 200
        assert 'href="/ops"' not in reader

        st, operator = _get(port, "/?mode=operator")
        assert st == 200
        assert 'href="/ops' in operator

        st, ops = _get(port, "/ops")
        assert st == 200
        assert "OVP Truth UI" in ops or "Workbench" in ops or "Objects Indexed" in ops
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)
