"""Phase 38 follow-up fixes — review-driven correctness/security tests.

Each test pins one regression spotted in PR #62 review (and the deferred
Stage B follow-ups). Grouping them here keeps the per-fix coverage close to
the diff that introduced it; once the patterns settle they can fold into the
relevant module's main test file.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from ovp_pipeline import concept_dedup
from ovp_pipeline.commands import working_memory
from ovp_pipeline.commands.link_suggest import run_link_suggest
from ovp_pipeline.commands.ui_server import (
    _event_matches_object,
    _render_workbench_page,
    create_server,
)
from ovp_pipeline.materializers.crystal import _crystal_id, materialize_crystal
from ovp_pipeline.mcp_server import MCPServer, _clamp
from ovp_pipeline.runtime import VaultLayout

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# Workbench: reflected XSS in `var pack = ...` script block (PR #48 finding)
# ---------------------------------------------------------------------------


def test_workbench_pack_json_escapes_script_close_sequence() -> None:
    """A pack name containing ``</script>`` must not close the surrounding
    block. The escape mirrors graph/visualize._safe_json — replace ``</``
    with ``<\\/`` so the JS string still parses but never terminates HTML."""
    html = _render_workbench_page(
        object_id="", requested_pack="</script><img src=x onerror=alert(1)>"
    )
    assert "</script><img" not in html.split("</script>")[0]
    assert "<\\/script>" in html


# ---------------------------------------------------------------------------
# MCP graph tools: clamp client-supplied integers
# ---------------------------------------------------------------------------


def test_clamp_constrains_to_range() -> None:
    assert _clamp(0, lo=1, hi=5, name="x") == 1
    assert _clamp(99, lo=1, hi=5, name="x") == 5
    assert _clamp("3", lo=1, hi=5, name="x") == 3


def test_clamp_rejects_non_integer() -> None:
    with pytest.raises(ValueError, match="must be an integer"):
        _clamp("abc", lo=1, hi=5, name="hop")


def test_graph_neighborhood_clamps_hop_and_max_nodes(temp_vault: Path) -> None:
    layout = VaultLayout.from_vault(temp_vault)
    layout.knowledge_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(layout.knowledge_db) as conn:
        conn.executescript("""
            CREATE TABLE pages_index (
                slug TEXT PRIMARY KEY, title TEXT, note_type TEXT, path TEXT, day_id TEXT
            );
            CREATE TABLE page_links (
                source_slug TEXT, target_slug TEXT, target_raw TEXT,
                link_type TEXT, line_number INTEGER
            );
            """)
        conn.execute(
            "INSERT INTO pages_index (slug, title, note_type, path, day_id) "
            "VALUES ('a', 'A', 'evergreen', 'A.md', '')"
        )
        conn.commit()

    server = MCPServer(temp_vault)
    # hop=999 / max_nodes=99999 must not blow up — they get clamped to 5/500.
    result = server.call_tool(
        "graph_neighborhood", {"object_id": "a", "hop": 999, "max_nodes": 99999}
    )
    assert "nodes" in result
    assert "_html_fragment" not in result


def test_graph_neighborhood_rejects_unknown_render() -> None:
    server = MCPServer(Path.cwd())
    request = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "graph_neighborhood",
            "arguments": {"object_id": "x", "render": "pdf"},
        },
    }
    reply = server.handle_line(json.dumps(request))
    assert reply is not None
    # ValueError now maps to INVALID_PARAMS (-32602), not INTERNAL_ERROR.
    assert reply["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# link_suggest: must materialize knowledge.db before the first SQLite read
# ---------------------------------------------------------------------------


def test_run_link_suggest_succeeds_on_fresh_vault(temp_vault: Path) -> None:
    """A vault with no .ovp/knowledge.db must not crash with "unable to open
    database file" — run_link_suggest should ensure the index exists first."""
    layout = VaultLayout.from_vault(temp_vault)
    assert not layout.knowledge_db.exists()
    summary = run_link_suggest(temp_vault)  # apply=False by default
    assert summary["applied"] is False
    assert layout.knowledge_db.exists()


# ---------------------------------------------------------------------------
# concept_dedup: fail fast when a duplicate is missing (atomicity)
# ---------------------------------------------------------------------------


def _make_cluster(canonical_path: Path, dup_path: Path) -> concept_dedup.DedupCluster:
    canonical = concept_dedup.DedupCandidate(
        slug="canonical",
        title="Canonical",
        path=canonical_path,
        size_bytes=canonical_path.stat().st_size if canonical_path.exists() else 0,
    )
    duplicate = concept_dedup.DedupCandidate(
        slug="dup-missing",
        title="Dup Missing",
        path=dup_path,  # intentionally points at a non-existent file
        size_bytes=0,
    )
    return concept_dedup.DedupCluster(
        canonical=canonical, duplicates=(duplicate,), min_similarity=0.9
    )


def test_apply_cluster_refuses_when_duplicate_missing(tmp_path: Path) -> None:
    canonical = tmp_path / "Canonical.md"
    canonical.write_text("---\ntitle: Canonical\n---\n\nbody\n", encoding="utf-8")
    other = tmp_path / "Other.md"
    other.write_text("body mentions [[Dup Missing]]\n", encoding="utf-8")

    cluster = _make_cluster(canonical, tmp_path / "Missing.md")
    result = concept_dedup.apply_cluster(tmp_path, cluster, dry_run=False)

    assert any("missing duplicate files" in err for err in result.errors)
    # Nothing should have been mutated — wikilink count untouched, no archive.
    assert other.read_text(encoding="utf-8") == "body mentions [[Dup Missing]]\n"
    assert result.wikilink_rewrites == 0
    assert result.archived == []


# ---------------------------------------------------------------------------
# working_memory: legacy "timestamp" field counts toward Pulse Highlights
# ---------------------------------------------------------------------------


def test_pulse_highlights_falls_back_to_timestamp(temp_vault: Path) -> None:
    layout = VaultLayout.from_vault(temp_vault)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    layout.pipeline_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2099-01-01T00:00:00Z",
                        "event_type": "legacy_event",
                    }
                ),
                json.dumps({"ts": "2099-01-01T00:00:00Z", "event_type": "modern_event"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    from datetime import datetime, timezone

    counts = working_memory._pulse_highlights(
        layout, since=datetime(2098, 1, 1, tzinfo=timezone.utc)
    )
    assert counts.get("legacy_event") == 1
    assert counts.get("modern_event") == 1


# ---------------------------------------------------------------------------
# /explore SSE: scope events to the requested object_id
# ---------------------------------------------------------------------------


def test_event_matches_object_top_level_and_arguments() -> None:
    assert _event_matches_object({"object_id": "alpha"}, "alpha")
    assert _event_matches_object({"arguments": {"object_id": "alpha", "hop": 1}}, "alpha")
    assert not _event_matches_object({"object_id": "beta"}, "alpha")
    assert not _event_matches_object({}, "alpha")


@pytest.fixture
def running_server(temp_vault: Path):
    server = create_server(temp_vault, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_explore_stream_filters_to_object_id(temp_vault: Path, running_server) -> None:
    """When the URL carries object_id=alpha, only events matching alpha must
    reach the client. A beta event written between polls is silently dropped."""
    host, port = running_server.server_address
    url = f"http://{host}:{port}/explore/stream" "?object_id=alpha&max_polls=8&poll_interval=0.05"
    body_holder: dict[str, str] = {}

    def consume() -> None:
        with _NO_PROXY_OPENER.open(url, timeout=10) as response:
            body_holder["body"] = response.read().decode("utf-8")

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()

    time.sleep(0.15)
    layout = VaultLayout.from_vault(temp_vault)
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    log = layout.logs_dir / "agent-decisions.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-04-24T12:00:00Z",
                "tool": "graph_neighborhood",
                "arguments": {"object_id": "beta"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "ts": "2026-04-24T12:00:01Z",
                "tool": "graph_neighborhood",
                "arguments": {"object_id": "alpha"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    consumer.join(timeout=5)
    body = body_holder.get("body", "")
    # Both alpha events round-trip; beta is filtered out by the SSE handler.
    assert '"object_id": "alpha"' in body
    assert '"object_id": "beta"' not in body


# ---------------------------------------------------------------------------
# Crystal idempotency: EVOLVES relations must affect crystal_id
# ---------------------------------------------------------------------------


def test_crystal_id_changes_when_evolves_changes() -> None:
    """Same snapshot + object_ids but different EVOLVES relations must produce
    distinct ids — otherwise yesterday's crystal file gets silently overwritten
    with new edges."""
    snapshot: dict = {"unresolved_issues": [], "insights": [], "priority_items": []}
    object_ids = ["a", "b"]
    from datetime import date

    when = date(2026, 4, 24)
    id_no_rels = _crystal_id(snapshot, object_ids, when=when)
    id_with_rels = _crystal_id(
        snapshot,
        object_ids,
        when=when,
        relations=[{"source": "a", "target": "b", "subtype": "replaces"}],
    )
    assert id_no_rels != id_with_rels


def test_materialize_crystal_round_trips_after_evolves_change(
    temp_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: same snapshot, change one EVOLVES edge, get a new file.

    We patch ``_query_evolves_relations`` because the production path calls
    ``ensure_knowledge_db_current`` which rebuilds the DB from the vault
    contents — directly seeding ``graph_edges`` would be wiped out.
    """
    from ovp_pipeline.materializers import crystal as crystal_module

    snapshot: dict = {
        "object_ids": ["a"],
        "unresolved_issues": [],
        "insights": [],
        "priority_items": [],
    }
    from datetime import date

    monkeypatch.setattr(crystal_module, "_query_evolves_relations", lambda *a, **kw: [])
    first = materialize_crystal(snapshot, temp_vault, when=date(2026, 4, 24))

    monkeypatch.setattr(
        crystal_module,
        "_query_evolves_relations",
        lambda *a, **kw: [{"source": "a", "target": "b", "subtype": "replaces"}],
    )
    second = materialize_crystal(snapshot, temp_vault, when=date(2026, 4, 24))

    assert second.crystal_id != first.crystal_id
    assert second.path != first.path
