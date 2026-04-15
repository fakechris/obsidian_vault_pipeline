from __future__ import annotations

from io import StringIO
import json
import sqlite3

import pytest


def test_rebuild_knowledge_index_creates_database_and_core_tables(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Linked.md"

    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-07
---

# Source Note

Links to [[linked-note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: linked-note
title: Linked Note
type: evergreen
date: 2026-04-07
---

# Linked Note
""",
        encoding="utf-8",
    )

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    assert result["pages_indexed"] == 2
    assert result["links_indexed"] == 1
    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }

    assert "pages_index" in tables
    assert "page_fts" in tables
    assert "page_links" in tables


def test_rebuild_knowledge_index_uses_canonical_note_id_and_resolved_links(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Wrong-File-Name.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Also-Wrong.md"

    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-07
---

# Source Note

Links to [[Linked Note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: linked-note
title: Linked Note
type: evergreen
date: 2026-04-07
aliases: [Linked Note]
---

# Linked Note
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    with sqlite3.connect(db_path) as conn:
        pages = conn.execute(
            "SELECT slug, title, note_type, path FROM pages_index ORDER BY slug"
        ).fetchall()
        links = conn.execute(
            "SELECT source_slug, target_slug, link_type FROM page_links"
        ).fetchall()

    assert pages == [
        ("linked-note", "Linked Note", "evergreen", str(target)),
        ("source-note", "Source Note", "evergreen", str(source)),
    ]
    assert links == [("source-note", "linked-note", "wikilink")]


def test_rebuild_knowledge_index_indexes_atlas_and_deep_dive_pages_for_bridge_queries(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    evergreen.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-07
---

# Alpha

Alpha body.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: deep-dive
title: Deep Dive
type: deep_dive
date: 2026-04-07
---

# Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-07
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    assert result["pages_indexed"] == 3
    assert result["objects_indexed"] == 1

    with sqlite3.connect(db_path) as conn:
        pages = conn.execute(
            "SELECT slug, note_type FROM pages_index ORDER BY slug"
        ).fetchall()
        links = conn.execute(
            "SELECT source_slug, target_slug, link_type FROM page_links ORDER BY source_slug"
        ).fetchall()

    assert pages == [
        ("alpha", "evergreen"),
        ("atlas-index", "moc"),
        ("deep-dive", "deep_dive"),
    ]
    assert links == [
        ("atlas-index", "alpha", "wikilink"),
        ("deep-dive", "alpha", "wikilink"),
    ]


def test_rebuild_knowledge_index_dedupes_duplicate_slugs_across_surfaces(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    evergreen.write_text(
        """---
note_id: alpha
title: Alpha Evergreen
type: evergreen
date: 2026-04-07
---

# Alpha Evergreen
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Alpha.md"
    atlas.write_text(
        """---
note_id: alpha
title: Alpha Atlas
type: moc
date: 2026-04-07
---

# Alpha Atlas
""",
        encoding="utf-8",
    )

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    assert result["pages_indexed"] == 1
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT slug, title, note_type, path FROM pages_index ORDER BY slug"
        ).fetchall()

    assert rows == [("alpha", "Alpha Evergreen", "evergreen", str(evergreen))]


def test_knowledge_index_help_includes_expected_arguments(capsys):
    from openclaw_pipeline.commands.knowledge_index import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "--vault-dir" in captured.out
    assert "--json" in captured.out


def test_knowledge_index_cli_rebuild_returns_json_summary(temp_vault, capsys):
    from openclaw_pipeline.commands.knowledge_index import main

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Linked.md"

    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-07
---

# Source Note

Links to [[linked-note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: linked-note
title: Linked Note
type: evergreen
date: 2026-04-07
---

# Linked Note
""",
        encoding="utf-8",
    )

    result = main(["--vault-dir", str(temp_vault), "--json"])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["pages_indexed"] == 2
    assert payload["links_indexed"] == 1
    assert payload["raw_records_indexed"] == 0
    assert payload["timeline_events_indexed"] == 2
    assert payload["audit_events_indexed"] == 0


def test_knowledge_index_cli_rebuild_passes_pack_override(temp_vault, capsys, monkeypatch):
    from openclaw_pipeline.commands.knowledge_index import main

    captured: dict[str, object] = {}

    def fake_rebuild(vault_dir, *, pack_name=None):
        captured["vault_dir"] = vault_dir
        captured["pack_name"] = pack_name
        return {
            "pages_indexed": 0,
            "links_indexed": 0,
            "raw_records_indexed": 0,
            "timeline_events_indexed": 0,
            "audit_events_indexed": 0,
            "db_path": str(temp_vault / "60-Logs" / "knowledge.db"),
        }

    monkeypatch.setattr(
        "openclaw_pipeline.commands.knowledge_index.rebuild_knowledge_index",
        fake_rebuild,
    )

    result = main(["--vault-dir", str(temp_vault), "--pack", "default-knowledge", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["pages_indexed"] == 0
    assert captured["pack_name"] == "default-knowledge"


def test_rebuild_knowledge_index_dispatches_truth_projection_via_registry(temp_vault, monkeypatch):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout
    from openclaw_pipeline.truth_store import TruthStoreProjection

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-07
---

# Source Note
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    class Spec:
        pack = "media-pack"

    def fake_execute_truth_projection_builder(*, vault_dir, page_rows, link_rows, pack_name=None):
        captured["vault_dir"] = vault_dir
        captured["pack_name"] = pack_name
        captured["page_rows"] = list(page_rows)
        captured["link_rows"] = list(link_rows)
        return (
            Spec(),
            TruthStoreProjection(
                objects=[],
                claims=[],
                claim_evidence=[],
                relations=[],
                compiled_summaries=[],
                contradictions=[],
            ),
        )

    monkeypatch.setattr(
        "openclaw_pipeline.knowledge_index.execute_truth_projection_builder",
        fake_execute_truth_projection_builder,
    )

    result = rebuild_knowledge_index(temp_vault, pack_name="media-pack")

    assert result["pages_indexed"] == 1
    assert result["projection_pack"] == "media-pack"
    assert captured["pack_name"] == "media-pack"
    assert captured["page_rows"]
    assert captured["link_rows"] == []
    assert VaultLayout.from_vault(temp_vault).knowledge_db.exists()


def test_rebuild_knowledge_index_mirrors_structured_sidecars_timeline_and_audit(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    evergreen.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-07
---

# Source Note

## Historical Notes

### 2026-04-05
Something happened.
""",
        encoding="utf-8",
    )

    layout = VaultLayout.from_vault(temp_vault)
    layout.link_resolution_dir.mkdir(parents=True, exist_ok=True)
    (layout.link_resolution_dir / "source-note.json").write_text(
        json.dumps(
            {
                "article": "source-note",
                "resolver_version": "v2",
                "area": "general",
                "decisions": [{"surface": "Linked Note", "action": "link_existing", "slug": "linked-note"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    layout.pipeline_log.parent.mkdir(parents=True, exist_ok=True)
    layout.pipeline_log.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-07T12:00:00Z",
                "session_id": "pipe-1",
                "event_type": "pipeline_stage_completed",
                "targets": ["source-note"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (layout.logs_dir / "refine-mutations.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-04-07T13:00:00Z",
                "session_id": "refine-1",
                "event_type": "refine_mutation_applied",
                "mode": "cleanup",
                "slug": "source-note",
                "status": "written",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = rebuild_knowledge_index(temp_vault)
    db_path = layout.knowledge_db

    assert result["raw_records_indexed"] == 1
    assert result["timeline_events_indexed"] == 2
    assert result["audit_events_indexed"] == 2

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        raw_rows = conn.execute(
            "SELECT slug, source_name FROM raw_data ORDER BY slug, source_name"
        ).fetchall()
        timeline_rows = conn.execute(
            "SELECT slug, event_date, event_type FROM timeline_events ORDER BY event_date, event_type"
        ).fetchall()
        audit_rows = conn.execute(
            "SELECT source_log, event_type FROM audit_events ORDER BY source_log, event_type"
        ).fetchall()

    assert "raw_data" in tables
    assert "timeline_events" in tables
    assert "audit_events" in tables
    assert raw_rows == [("source-note", "link_resolution")]
    assert timeline_rows == [
        ("source-note", "2026-04-05", "heading_date"),
        ("source-note", "2026-04-07", "page_date"),
    ]
    assert audit_rows == [
        ("pipeline", "pipeline_stage_completed"),
        ("refine", "refine_mutation_applied"),
    ]


def test_rebuild_knowledge_index_creates_section_chunk_embeddings(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    evergreen.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
Agent harness architecture orchestrates tools and execution layers.

## Usage Patterns
Usage patterns cover prompts, workflows, and operator loops.
""",
        encoding="utf-8",
    )

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    assert result["embedding_chunks_indexed"] == 2

    with sqlite3.connect(db_path) as conn:
        chunks = conn.execute(
            "SELECT slug, chunk_index, section_title, embedding_model FROM page_embeddings ORDER BY chunk_index"
        ).fetchall()

    assert chunks == [
        ("agent-harness", 0, "Architecture", "local-hash-v1"),
        ("agent-harness", 1, "Usage Patterns", "local-hash-v1"),
    ]


def test_query_knowledge_index_returns_best_matching_chunk(temp_vault):
    from openclaw_pipeline.knowledge_index import query_knowledge_index, rebuild_knowledge_index

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    evergreen.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
Agent harness architecture orchestrates tools and execution layers.

## Usage Patterns
Usage patterns cover prompts, workflows, and operator loops.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    results = query_knowledge_index(temp_vault, "architecture execution layers", limit=2)

    assert len(results) == 2
    assert results[0]["slug"] == "agent-harness"
    assert results[0]["section_title"] == "Architecture"
    assert results[0]["score"] >= results[1]["score"]


def test_knowledge_index_cli_query_returns_ranked_json_results(temp_vault, capsys):
    from openclaw_pipeline.commands.knowledge_index import main

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    evergreen.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
Agent harness architecture orchestrates tools and execution layers.

## Usage Patterns
Usage patterns cover prompts, workflows, and operator loops.
""",
        encoding="utf-8",
    )

    main(["--vault-dir", str(temp_vault), "--json"])
    capsys.readouterr()

    result = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--query",
            "architecture execution layers",
            "--limit",
            "1",
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["query"] == "architecture execution layers"
    assert payload["results"][0]["slug"] == "agent-harness"
    assert payload["results"][0]["section_title"] == "Architecture"


def test_search_knowledge_index_returns_ranked_pages(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index, search_knowledge_index

    first = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    second = temp_vault / "10-Knowledge" / "Evergreen" / "Prompt-Patterns.md"
    first.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

Agent harness architecture manages tools and execution layers.
""",
        encoding="utf-8",
    )
    second.write_text(
        """---
note_id: prompt-patterns
title: Prompt Patterns
type: evergreen
date: 2026-04-07
---

# Prompt Patterns

Prompt patterns improve instruction design and tool use.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    results = search_knowledge_index(temp_vault, "architecture tools", limit=2)

    assert len(results) == 2
    assert results[0]["slug"] == "agent-harness"
    assert results[0]["title"] == "Agent Harness"


def test_get_knowledge_page_returns_canonical_page_payload(temp_vault):
    from openclaw_pipeline.knowledge_index import get_knowledge_page, rebuild_knowledge_index

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
aliases: [Harness]
---

# Agent Harness

Body text.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    page = get_knowledge_page(temp_vault, "agent-harness")

    assert page["slug"] == "agent-harness"
    assert page["title"] == "Agent Harness"
    assert page["note_type"] == "evergreen"
    assert "Body text." in page["body"]
    assert page["frontmatter"]["note_id"] == "agent-harness"


def test_knowledge_index_stats_returns_core_counts(temp_vault):
    from openclaw_pipeline.knowledge_index import knowledge_index_stats, rebuild_knowledge_index

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
Body text.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    stats = knowledge_index_stats(temp_vault)

    assert stats["pages"] == 1
    assert stats["links"] == 0
    assert stats["raw_records"] == 0
    assert stats["timeline_events"] == 1
    assert stats["audit_events"] == 0
    assert stats["embedding_chunks"] == 1


def test_recent_audit_events_returns_newest_rows_first(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index, recent_audit_events
    from openclaw_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness
""",
        encoding="utf-8",
    )

    layout = VaultLayout.from_vault(temp_vault)
    layout.pipeline_log.parent.mkdir(parents=True, exist_ok=True)
    layout.pipeline_log.write_text(
        json.dumps(
            {"timestamp": "2026-04-07T11:00:00Z", "session_id": "s1", "event_type": "older", "slug": "agent-harness"},
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {"timestamp": "2026-04-07T12:00:00Z", "session_id": "s2", "event_type": "newer", "slug": "agent-harness"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    events = recent_audit_events(temp_vault, limit=2, source_log="pipeline")

    assert [event["event_type"] for event in events] == ["newer", "older"]


def test_knowledge_index_cli_read_modes_return_json(temp_vault, capsys):
    from openclaw_pipeline.commands.knowledge_index import main
    from openclaw_pipeline.runtime import VaultLayout

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
Agent harness architecture manages tools and execution layers.
""",
        encoding="utf-8",
    )
    layout = VaultLayout.from_vault(temp_vault)
    layout.pipeline_log.parent.mkdir(parents=True, exist_ok=True)
    layout.pipeline_log.write_text(
        json.dumps(
            {"timestamp": "2026-04-07T12:00:00Z", "session_id": "s1", "event_type": "pipeline_stage_completed", "slug": "agent-harness"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    main(["--vault-dir", str(temp_vault), "--json"])
    capsys.readouterr()

    assert main(["--vault-dir", str(temp_vault), "--search", "architecture", "--limit", "1", "--json"]) == 0
    search_payload = json.loads(capsys.readouterr().out)
    assert search_payload["results"][0]["slug"] == "agent-harness"

    assert main(["--vault-dir", str(temp_vault), "--get", "agent-harness", "--json"]) == 0
    get_payload = json.loads(capsys.readouterr().out)
    assert get_payload["page"]["slug"] == "agent-harness"

    assert main(["--vault-dir", str(temp_vault), "--stats", "--json"]) == 0
    stats_payload = json.loads(capsys.readouterr().out)
    assert stats_payload["stats"]["pages"] == 1

    assert main(["--vault-dir", str(temp_vault), "--audit-recent", "1", "--source-log", "pipeline", "--json"]) == 0
    audit_payload = json.loads(capsys.readouterr().out)
    assert audit_payload["events"][0]["event_type"] == "pipeline_stage_completed"


def test_knowledge_tools_json_exposes_expected_read_tools():
    from openclaw_pipeline.knowledge_index import knowledge_tools_json

    tools = knowledge_tools_json()
    tool_names = {tool["name"] for tool in tools}

    assert {
        "knowledge_search",
        "knowledge_query",
        "knowledge_truth_search",
        "knowledge_contradictions",
        "knowledge_get",
        "knowledge_stats",
        "knowledge_audit_recent",
    }.issubset(tool_names)


def test_dispatch_knowledge_tool_routes_to_read_helpers(temp_vault):
    from openclaw_pipeline.knowledge_index import dispatch_knowledge_tool

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
Agent harness architecture manages tools and execution layers.
""",
        encoding="utf-8",
    )

    search_result = dispatch_knowledge_tool(temp_vault, "knowledge_search", {"query": "architecture", "limit": 1})
    truth_result = dispatch_knowledge_tool(temp_vault, "knowledge_truth_search", {"query": "architecture", "limit": 1})
    contradictions_result = dispatch_knowledge_tool(temp_vault, "knowledge_contradictions", {"limit": 5})
    get_result = dispatch_knowledge_tool(temp_vault, "knowledge_get", {"slug": "agent-harness"})
    stats_result = dispatch_knowledge_tool(temp_vault, "knowledge_stats", {})

    assert search_result["results"][0]["slug"] == "agent-harness"
    assert truth_result["results"]
    assert truth_result["results"][0]["object_id"] == "agent-harness"
    assert truth_result["results"][0]["claim_kind"] == "page_summary"
    assert contradictions_result["items"] == []
    assert get_result["page"]["slug"] == "agent-harness"
    assert stats_result["stats"]["pages"] == 1


def test_serve_knowledge_index_processes_jsonl_requests(temp_vault):
    from openclaw_pipeline.knowledge_index import serve_knowledge_index

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## Architecture
Agent harness architecture manages tools and execution layers.
""",
        encoding="utf-8",
    )

    stdin = StringIO(json.dumps({"tool": "knowledge_search", "args": {"query": "architecture", "limit": 1}}) + "\n")
    stdout = StringIO()

    serve_knowledge_index(temp_vault, stdin, stdout)

    response = json.loads(stdout.getvalue().strip())
    assert response["ok"] is True
    assert response["result"]["results"][0]["slug"] == "agent-harness"


def test_knowledge_index_cli_tools_json_lists_tools(capsys):
    from openclaw_pipeline.commands.knowledge_index import main

    result = main(["--tools-json"])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    tool_names = {tool["name"] for tool in payload}
    assert "knowledge_query" in tool_names


def test_rebuild_knowledge_index_preserves_existing_db_on_failure(temp_vault, monkeypatch):
    from openclaw_pipeline.knowledge_index import knowledge_index_stats, rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    evergreen.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note

## Architecture
Stable body.
""",
        encoding="utf-8",
    )

    first = rebuild_knowledge_index(temp_vault)
    assert first["pages_indexed"] == 1

    def fail_embed(_: str, dimensions: int = 128) -> bytes:
        raise RuntimeError("boom")

    monkeypatch.setattr("openclaw_pipeline.knowledge_index._embed_text", fail_embed)

    with pytest.raises(RuntimeError, match="boom"):
        rebuild_knowledge_index(temp_vault)

    stats = knowledge_index_stats(temp_vault)
    assert stats["pages"] == 1
    assert stats["embedding_chunks"] == 1
    assert VaultLayout.from_vault(temp_vault).knowledge_db.exists()


def test_rebuild_knowledge_index_cleans_stale_temp_sqlite_sidecars(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    evergreen.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note

## Architecture
Stable body.
""",
        encoding="utf-8",
    )

    layout = VaultLayout.from_vault(temp_vault)
    temp_db = layout.knowledge_db.with_name(f"{layout.knowledge_db.name}.tmp")
    temp_db.parent.mkdir(parents=True, exist_ok=True)
    for artifact in (
        temp_db,
        temp_db.with_name(f"{temp_db.name}-wal"),
        temp_db.with_name(f"{temp_db.name}-shm"),
    ):
        artifact.write_bytes(b"stale")

    result = rebuild_knowledge_index(temp_vault)

    assert result["pages_indexed"] == 1
    assert layout.knowledge_db.exists()


def test_rebuild_knowledge_index_does_not_reenter_knowledge_discovery(temp_vault, monkeypatch):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Target.md"

    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note

Links to [[Target Note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: target-note
title: Target Note
type: evergreen
date: 2026-04-10
aliases: [Target Note]
---

# Target Note
""",
        encoding="utf-8",
    )

    layout = VaultLayout.from_vault(temp_vault)
    rebuild_knowledge_index(temp_vault)
    layout.knowledge_db.unlink()

    def fail_related(*args, **kwargs):
        raise AssertionError("knowledge discovery should not run during rebuild")

    monkeypatch.setattr("openclaw_pipeline.concept_registry.discover_related", fail_related)

    result = rebuild_knowledge_index(temp_vault)

    assert result["pages_indexed"] == 2
    assert result["links_indexed"] == 1


def test_rebuild_knowledge_index_acquires_single_writer_lock(temp_vault, monkeypatch):
    from contextlib import contextmanager

    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    evergreen.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note
""",
        encoding="utf-8",
    )

    calls: list[str] = []

    @contextmanager
    def fake_lock(vault_dir, *, timeout_seconds=300.0):
        calls.append(f"enter:{temp_vault == vault_dir}")
        yield
        calls.append("exit")

    monkeypatch.setattr("openclaw_pipeline.knowledge_index.knowledge_db_write_lock", fake_lock)

    result = rebuild_knowledge_index(temp_vault)

    assert result["pages_indexed"] == 1
    assert calls == ["enter:True", "exit"]
