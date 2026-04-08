from __future__ import annotations

import json


def test_discover_related_defaults_to_knowledge_engine(temp_vault):
    from openclaw_pipeline.discovery import discover_related
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
aliases: [Harness Runtime]
---

# Agent Harness

## Architecture

The harness coordinates architecture, execution, and tools.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    results = discover_related(temp_vault, "architecture tools", limit=3)

    assert results
    assert results[0]["engine"] == "knowledge"
    assert results[0]["slug"] == "agent-harness"
    assert results[0]["kind"] in {"lexical", "semantic"}
    assert results[0]["pack"] == "default-knowledge"
    assert results[0]["object_kind"] == "document"
    assert "title" in results[0]
    assert "snippet" in results[0]


def test_discover_related_qmd_engine_is_explicit_and_typed(temp_vault, monkeypatch):
    from openclaw_pipeline import discovery

    def fake_qmd(vault_dir, query, limit):  # noqa: ARG001
        return [
            {
                "engine": "qmd",
                "kind": "semantic",
                "slug": "agent-runtime",
                "title": "Agent Runtime",
                "score": 0.91,
                "snippet": "runtime orchestration",
            }
        ]

    monkeypatch.setattr(discovery, "_discover_with_qmd", fake_qmd)
    results = discovery.discover_related(temp_vault, "runtime orchestration", engine="qmd", limit=2)

    assert results == [
        {
            "engine": "qmd",
            "kind": "semantic",
            "slug": "agent-runtime",
            "title": "Agent Runtime",
            "score": 0.91,
            "snippet": "runtime orchestration",
            "pack": "default-knowledge",
            "object_kind": "document",
        }
    ]


def test_discover_with_knowledge_deduplicates_by_slug_and_skips_semantic_when_lexical_is_enough(
    temp_vault,
    monkeypatch,
):
    from openclaw_pipeline import discovery

    monkeypatch.setattr(
        discovery,
        "_safe_search_knowledge",
        lambda vault_dir, query, limit: [  # noqa: ARG005
            {"slug": "agent-harness", "title": "Agent Harness", "score": 12.0},
            {"slug": "agent-runtime", "title": "Agent Runtime", "score": 11.0},
        ],
    )

    def fail_semantic(vault_dir, query, limit):  # noqa: ARG001
        raise AssertionError("semantic search should not run when lexical already satisfies the limit")

    monkeypatch.setattr(
        "openclaw_pipeline.knowledge_index.get_knowledge_page",
        lambda vault_dir, slug: {"title": slug.replace("-", " ").title(), "path": f"{slug}.md", "body": slug},
    )
    monkeypatch.setattr("openclaw_pipeline.knowledge_index.query_knowledge_index", fail_semantic)

    results = discovery._discover_with_knowledge(temp_vault, "agent runtime", limit=2)

    assert [row["slug"] for row in results] == ["agent-harness", "agent-runtime"]
    assert {row["kind"] for row in results} == {"lexical"}
