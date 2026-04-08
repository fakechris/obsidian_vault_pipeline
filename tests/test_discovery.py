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
        }
    ]

