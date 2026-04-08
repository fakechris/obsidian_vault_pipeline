from __future__ import annotations

import json


def test_build_evidence_payload_separates_identity_retrieval_graph_and_audit(temp_vault):
    from openclaw_pipeline.concept_registry import ConceptEntry, ConceptRegistry
    from openclaw_pipeline.evidence import build_evidence_payload
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Target.md"

    source.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
aliases: [Harness Runtime]
---

# Agent Harness

## Architecture

The harness coordinates architecture and tools.

Links to [[agent-runtime]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: agent-runtime
title: Agent Runtime
type: evergreen
date: 2026-04-07
---

# Agent Runtime
""",
        encoding="utf-8",
    )

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="agent-harness",
            title="Agent Harness",
            aliases=["Harness Runtime"],
            definition="Harness.",
            area="AI",
        )
    )
    registry.save()

    rebuild_knowledge_index(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)
    layout.pipeline_log.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-07T12:00:00Z",
                "session_id": "pipe-1",
                "event_type": "pipeline_stage_completed",
                "targets": ["agent-harness"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_evidence_payload(
        temp_vault,
        query="architecture tools",
        mentions=["Agent Harness"],
        slugs=["agent-harness"],
        limit=3,
    )

    assert set(payload) == {
        "identity_evidence",
        "retrieval_evidence",
        "graph_evidence",
        "audit_evidence",
    }
    assert payload["identity_evidence"]
    assert payload["identity_evidence"][0]["channel"] == "identity"
    assert payload["identity_evidence"][0]["entry_slug"] == "agent-harness"
    assert payload["retrieval_evidence"]
    assert all(item["channel"] == "retrieval" for item in payload["retrieval_evidence"])
    assert payload["graph_evidence"]
    assert all(item["channel"] == "graph" for item in payload["graph_evidence"])
    assert payload["audit_evidence"]
    assert all(item["channel"] == "audit" for item in payload["audit_evidence"])


def test_query_tool_attaches_structured_evidence_to_saved_answer(temp_vault, monkeypatch):
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

    monkeypatch.setattr(
        query_tool.VaultQuerier,
        "search",
        lambda self, query, top_k=10, engine="knowledge": [
            query_tool.SearchResult(
                file="10-Knowledge/Evergreen/Agent-Harness.md",
                title="Agent Harness",
                relevance=1.0,
                excerpt="architecture",
            )
        ],
    )
    monkeypatch.setattr(
        query_tool.VaultQuerier,
        "query",
        lambda self, q, r: {"answer": "ok", "sources": [], "related_concepts": []},
    )

    captured = {}

    def fake_save(self, question, result, output_dir, output_format="markdown"):  # noqa: ARG001
        captured["result"] = result
        return output_dir / "saved.md"

    monkeypatch.setattr(query_tool.VaultQuerier, "save_to_wiki", fake_save)
    monkeypatch.setattr(
        query_tool,
        "build_evidence_payload",
        lambda vault_dir, **kwargs: {
            "identity_evidence": [{"channel": "identity"}],
            "retrieval_evidence": [{"channel": "retrieval"}],
            "graph_evidence": [{"channel": "graph"}],
            "audit_evidence": [{"channel": "audit"}],
        },
    )

    result = query_tool.main(["--vault-dir", str(temp_vault), "architecture tools"])

    assert result == 0
    assert "evidence" in captured["result"]
    assert captured["result"]["evidence"]["identity_evidence"][0]["channel"] == "identity"


def test_cleanup_proposal_includes_structured_evidence(temp_vault, capsys):
    from openclaw_pipeline.commands.cleanup import main

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

## 2026-04

Old notes.
""",
        encoding="utf-8",
    )

    result = main(["--vault-dir", str(temp_vault), "--all", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["proposals"]
    assert "evidence" in payload["proposals"][0]
    assert set(payload["proposals"][0]["evidence"]) == {
        "identity_evidence",
        "retrieval_evidence",
        "graph_evidence",
        "audit_evidence",
    }
