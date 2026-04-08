from __future__ import annotations

import json

import pytest


def test_vault_querier_search_defaults_to_knowledge_engine(temp_vault, monkeypatch):
    from openclaw_pipeline import query_tool

    queried = {}

    def fake_discover(vault_dir, query, engine, limit):
        queried["vault_dir"] = vault_dir
        queried["query"] = query
        queried["engine"] = engine
        queried["limit"] = limit
        return [
            {
                "engine": "knowledge",
                "kind": "lexical",
                "slug": "agent-harness",
                "title": "Agent Harness",
                "score": 12.3,
                "snippet": "architecture and tools",
                "path": "10-Knowledge/Evergreen/Agent-Harness.md",
            }
        ]

    monkeypatch.setattr(query_tool, "discover_related", fake_discover)
    querier = query_tool.VaultQuerier(temp_vault)
    results = querier.search("architecture tools", top_k=5)

    assert queried["engine"] == "knowledge"
    assert len(results) == 1
    assert results[0].file == "10-Knowledge/Evergreen/Agent-Harness.md"
    assert results[0].title == "Agent Harness"


def test_query_cli_explicit_qmd_engine_is_passed(temp_vault, monkeypatch, capsys):
    from openclaw_pipeline import query_tool

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    source.write_text(
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

    captured = {}

    def fake_search(self, query, top_k=10, engine="knowledge"):
        captured["engine"] = engine
        return [
            query_tool.SearchResult(
                file="10-Knowledge/Evergreen/Agent-Harness.md",
                title="Agent Harness",
                relevance=1.0,
                excerpt="architecture",
            )
        ]

    monkeypatch.setattr(query_tool.VaultQuerier, "search", fake_search)
    monkeypatch.setattr(query_tool.VaultQuerier, "query", lambda self, q, r: {"answer": "ok", "sources": [], "related_concepts": []})
    monkeypatch.setattr(query_tool.VaultQuerier, "save_to_wiki", lambda self, q, a, d, output_format="markdown": d / "saved.md")

    result = query_tool.main([
        "--vault-dir", str(temp_vault),
        "--engine", "qmd",
        "--top-k", "3",
        "runtime architecture",
    ])
    capsys.readouterr()

    assert result == 0
    assert captured["engine"] == "qmd"


def test_query_cli_explicit_qmd_engine_fails_clearly_when_unavailable(temp_vault, monkeypatch, capsys):
    from openclaw_pipeline import query_tool

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    source.write_text(
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

    monkeypatch.setattr(query_tool.VaultQuerier, "search", lambda self, query, top_k=10, engine="knowledge": (_ for _ in ()).throw(RuntimeError("QMD engine requested but qmd is not available")))

    result = query_tool.main([
        "--vault-dir", str(temp_vault),
        "--engine", "qmd",
        "runtime architecture",
    ])
    captured = capsys.readouterr()

    assert result == 1
    assert "qmd is not available" in captured.out.lower()

