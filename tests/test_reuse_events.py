from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline import knowledge_index
from ovp_pipeline.runtime import VaultLayout


def _write_evergreen(vault: Path, slug: str, title: str) -> Path:
    path = vault / "10-Knowledge" / "Evergreen" / f"{title.replace(' ', '-')}.md"
    path.write_text(
        f"""---
note_id: {slug}
title: {title}
type: evergreen
date: 2026-04-22
---

# {title}

{title} is a stable evergreen used in reuse-event tests.
""",
        encoding="utf-8",
    )
    return path


def _write_reuse_event(vault: Path, **fields: object) -> dict[str, object]:
    """Write a fully-formed event to 60-Logs/reuse-events.jsonl."""
    log_dir = vault / "60-Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "reuse-events.jsonl"
    event = {
        "event_id": fields.get("event_id"),
        "ts": fields.get("ts"),
        "session_id": fields.get("session_id", "test-session"),
        "pack": fields.get("pack", "research-tech"),
        "event_type": fields.get("event_type", "trusted_reuse_event"),
        "object_id": fields.get("object_id", ""),
        "object_kind": fields.get("object_kind", "evergreen"),
        "surface": fields.get("surface", "query"),
        "consumer_ref": fields.get("consumer_ref", ""),
        "evidence_present": int(bool(fields.get("evidence_present", 1))),
        "provenance_clean": int(bool(fields.get("provenance_clean", 1))),
        "trusted": int(bool(fields.get("trusted", 1))),
        "source_slug": fields.get("source_slug", ""),
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def test_collect_reuse_rows_uses_shared_event_iterator(temp_vault, monkeypatch):
    event = {
        "event_id": "evt-shared",
        "ts": "2026-04-22T12:00:00Z",
        "session_id": "session",
        "pack": "research-tech",
        "event_type": "trusted_reuse_event",
        "object_id": "alpha",
        "object_kind": "evergreen",
        "surface": "query",
        "consumer_ref": "report.md",
        "evidence_present": 1,
        "provenance_clean": 1,
        "trusted": 1,
    }
    monkeypatch.setattr(
        knowledge_index,
        "iter_for_index",
        lambda layout, log_name: iter([event]) if log_name == "reuse-events.jsonl" else iter(()),
    )

    rows = knowledge_index._collect_reuse_rows(VaultLayout.from_vault(temp_vault))

    assert len(rows) == 1
    assert rows[0][0] == "evt-shared"


def test_event_emitter_appends_jsonl_with_metadata(temp_vault):
    from ovp_pipeline.event_emitter import collect_for_index, emit
    from ovp_pipeline.runtime import VaultLayout

    event = emit(
        temp_vault,
        "reuse-events.jsonl",
        "trusted_reuse_event",
        {"object_id": "alpha", "surface": "query", "trusted": 1},
        session_id="sess-1",
        pack="research-tech",
    )

    layout = VaultLayout.from_vault(temp_vault)
    log_path = layout.logs_dir / "reuse-events.jsonl"
    assert log_path.exists()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["event_id"] == event["event_id"]
    assert row["session_id"] == "sess-1"
    assert row["pack"] == "research-tech"
    assert row["event_type"] == "trusted_reuse_event"
    assert row["object_id"] == "alpha"
    assert "ts" in row and row["ts"]

    collected = collect_for_index(layout, "reuse-events.jsonl")
    assert len(collected) == 1
    assert collected[0]["event_id"] == event["event_id"]


def test_emit_then_rebuild_creates_reuse_events_row(temp_vault):
    from ovp_pipeline.event_emitter import emit
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    from ovp_pipeline.runtime import VaultLayout

    _write_evergreen(temp_vault, "alpha", "Alpha")

    event = emit(
        temp_vault,
        "reuse-events.jsonl",
        "trusted_reuse_event",
        {
            "object_id": "alpha",
            "object_kind": "evergreen",
            "surface": "query",
            "consumer_ref": "what is alpha",
            "evidence_present": 1,
            "provenance_clean": 1,
            "trusted": 1,
            "source_slug": "alpha",
        },
        pack="research-tech",
    )

    result = rebuild_knowledge_index(temp_vault, pack_name="research-tech")
    assert result["reuse_events_indexed"] == 1

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT event_id, pack, surface, object_id, trusted FROM reuse_events"
        ).fetchall()

    assert rows == [(event["event_id"], "research-tech", "query", "alpha", 1)]


def test_rebuild_is_idempotent_for_reuse_events(temp_vault):
    from ovp_pipeline.event_emitter import emit
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    from ovp_pipeline.runtime import VaultLayout

    _write_evergreen(temp_vault, "alpha", "Alpha")
    emit(
        temp_vault,
        "reuse-events.jsonl",
        "trusted_reuse_event",
        {"object_id": "alpha", "surface": "query", "trusted": 1},
        pack="research-tech",
    )

    rebuild_knowledge_index(temp_vault, pack_name="research-tech")
    rebuild_knowledge_index(temp_vault, pack_name="research-tech")

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM reuse_events").fetchone()[0]
    assert count == 1


def test_emit_reuse_events_marks_trusted_when_object_has_evidence(temp_vault):
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    from ovp_pipeline.reuse_emitter import emit_reuse_events

    _write_evergreen(temp_vault, "alpha", "Alpha")
    rebuild_knowledge_index(temp_vault, pack_name="research-tech")

    events = emit_reuse_events(
        temp_vault,
        pack="research-tech",
        slugs=["alpha"],
        surface="query",
        consumer_ref="what is alpha",
    )

    assert len(events) == 1
    assert events[0]["trusted"] == 1
    assert events[0]["evidence_present"] == 1
    assert events[0]["provenance_clean"] == 1
    assert events[0]["object_id"] == "alpha"


def test_emit_reuse_events_skips_unknown_slugs(temp_vault):
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    from ovp_pipeline.reuse_emitter import emit_reuse_events

    _write_evergreen(temp_vault, "alpha", "Alpha")
    rebuild_knowledge_index(temp_vault, pack_name="research-tech")

    events = emit_reuse_events(
        temp_vault,
        pack="research-tech",
        slugs=["does-not-exist"],
        surface="query",
        consumer_ref="ghost",
    )

    assert events == []


def test_emit_reuse_events_marks_untrusted_when_broken_link_recent(temp_vault):
    from datetime import datetime, timezone

    from ovp_pipeline.knowledge_index import rebuild_knowledge_index
    from ovp_pipeline.reuse_emitter import emit_reuse_events
    from ovp_pipeline.runtime import VaultLayout

    _write_evergreen(temp_vault, "alpha", "Alpha")

    layout = VaultLayout.from_vault(temp_vault)
    layout.pipeline_log.parent.mkdir(parents=True, exist_ok=True)
    layout.pipeline_log.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "session_id": "lint-1",
                "event_type": "broken_link",
                "slug": "alpha",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault, pack_name="research-tech")

    events = emit_reuse_events(
        temp_vault,
        pack="research-tech",
        slugs=["alpha"],
        surface="query",
        consumer_ref="what is alpha",
    )

    assert len(events) == 1
    assert events[0]["evidence_present"] == 1
    assert events[0]["provenance_clean"] == 0
    assert events[0]["trusted"] == 0


def test_emit_crystal_reuse_events_writes_jsonl_with_crystal_kinds(temp_vault):
    """BL-058 follow-up: ``crystal_scoring._reuse_recency_signal``
    reads ``reuse_events`` rows with
    ``object_kind in {community_crystal, contradiction_crystal}``
    but no surface emitted any.  ``emit_crystal_reuse_events``
    bypasses the objects-table resolver (crystals don't live there)
    and writes the row directly so the signal has a real producer.
    """
    from ovp_pipeline.reuse_emitter import emit_crystal_reuse_events

    events = emit_crystal_reuse_events(
        temp_vault,
        pack="research-tech",
        crystals=[
            ("community_crystal", "cluster::aaa"),
            ("contradiction_crystal", "contradiction::bbb"),
            # Duplicate — must be deduped to a single event.
            ("community_crystal", "cluster::aaa"),
            # Invalid kind — silently dropped.
            ("not_a_crystal", "ignore"),
            # Empty id — dropped.
            ("community_crystal", ""),
        ],
        surface="atlas",
        consumer_ref="top_n=30",
    )
    assert len(events) == 2
    by_id = {e["object_id"]: e for e in events}
    assert by_id["cluster::aaa"]["object_kind"] == "community_crystal"
    assert by_id["contradiction::bbb"]["object_kind"] == "contradiction_crystal"
    # ``trusted`` is pinned to 1 because every crystal in the table
    # is by definition a synthesized artifact with full lineage.
    for e in events:
        assert e["evidence_present"] == 1
        assert e["provenance_clean"] == 1
        assert e["trusted"] == 1
        assert e["surface"] == "atlas"
        assert e["consumer_ref"] == "top_n=30"


def test_extract_cited_slugs_orders_and_deduplicates():
    from ovp_pipeline.reuse_emitter import extract_cited_slugs

    payload = {
        "identity_evidence": [
            {"entry_slug": "alpha"},
            {"entry_slug": ""},
            {"entry_slug": "beta"},
        ],
        "retrieval_evidence": [
            {"slug": "beta"},
            {"slug": "gamma"},
        ],
    }

    assert extract_cited_slugs(payload) == ["alpha", "beta", "gamma"]


def test_reuse_weekly_aggregates_by_iso_week_pack_surface(temp_vault, capsys):
    from ovp_pipeline.commands.reuse_report import main
    from ovp_pipeline.knowledge_index import rebuild_knowledge_index

    _write_evergreen(temp_vault, "alpha", "Alpha")

    _write_reuse_event(
        temp_vault,
        event_id="ev-1",
        ts="2026-04-20T10:00:00Z",  # ISO week 2026-W17
        pack="research-tech",
        surface="query",
        object_id="alpha",
        source_slug="alpha",
        trusted=1,
        evidence_present=1,
        provenance_clean=1,
    )
    _write_reuse_event(
        temp_vault,
        event_id="ev-2",
        ts="2026-04-21T10:00:00Z",  # same ISO week
        pack="research-tech",
        surface="query",
        object_id="alpha",
        source_slug="alpha",
        trusted=0,
        evidence_present=0,
        provenance_clean=1,
    )
    _write_reuse_event(
        temp_vault,
        event_id="ev-3",
        ts="2026-04-28T10:00:00Z",  # ISO week 2026-W18
        pack="research-tech",
        surface="briefing",
        object_id="alpha",
        source_slug="alpha",
        trusted=1,
        evidence_present=1,
        provenance_clean=1,
    )

    rebuild_knowledge_index(temp_vault, pack_name="research-tech")

    rc = main(["weekly", "--vault-dir", str(temp_vault), "--pack", "research-tech", "--json"])
    captured = capsys.readouterr()
    assert rc == 0

    payload = json.loads(captured.out)
    weekly = {
        (row["iso_week"], row["pack"], row["surface"]): (row["events"], row["trusted_events"])
        for row in payload["weekly"]
    }
    assert weekly[("2026-W17", "research-tech", "query")] == (2, 1)
    assert weekly[("2026-W18", "research-tech", "briefing")] == (1, 1)


def test_knowledge_db_pack_schema_check_requires_reuse_events(tmp_path):
    from ovp_pipeline.knowledge_index import _knowledge_db_supports_pack_schema

    db_path = tmp_path / "knowledge.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE pages_index (slug TEXT);
            CREATE TABLE timeline_events (
                slug TEXT, event_date TEXT, event_type TEXT,
                heading TEXT, payload_json TEXT
            );
            CREATE TABLE objects (pack TEXT);
            CREATE TABLE claims (pack TEXT);
            CREATE TABLE claim_evidence (pack TEXT);
            CREATE TABLE relations (pack TEXT);
            CREATE TABLE compiled_summaries (pack TEXT);
            CREATE TABLE contradictions (pack TEXT);
            CREATE TABLE graph_edges (pack TEXT);
            CREATE TABLE graph_clusters (pack TEXT);
            CREATE TABLE truth_projections (
                pack TEXT, owner_pack TEXT, builder_name TEXT, built_at TEXT
            );
            -- intentionally no reuse_events table
            """
        )

    assert _knowledge_db_supports_pack_schema(db_path) is False
