from __future__ import annotations

import json


def test_default_pack_discovery_preserves_knowledge_behavior_with_context(temp_vault):
    from openclaw_pipeline.concept_registry import ConceptEntry, ConceptRegistry
    from openclaw_pipeline.discovery import discover_related
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index

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

The harness coordinates architecture, execution, and tools.
""",
        encoding="utf-8",
    )

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="agent-harness",
            title="Agent Harness",
            aliases=[],
            definition="Harness.",
            area="AI",
            kind="concept",
        )
    )
    registry.save()

    rebuild_knowledge_index(temp_vault)
    results = discover_related(temp_vault, "architecture tools", limit=3, pack="default-knowledge")

    assert results
    assert results[0]["engine"] == "knowledge"
    assert results[0]["slug"] == "agent-harness"
    assert results[0]["pack"] == "default-knowledge"
    assert results[0]["object_kind"] == "concept"


def test_pack_discovery_hooks_can_filter_discoverable_object_kinds(temp_vault):
    from openclaw_pipeline.concept_registry import ConceptEntry, ConceptRegistry
    from openclaw_pipeline.discovery import discover_related
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.packs.base import BaseDomainPack

    concept_note = temp_vault / "10-Knowledge" / "Evergreen" / "Agent-Harness.md"
    concept_note.write_text(
        """---
note_id: agent-harness
title: Agent Harness
type: evergreen
date: 2026-04-07
---

# Agent Harness

Entity and orchestration reference.
""",
        encoding="utf-8",
    )
    entity_note = temp_vault / "10-Knowledge" / "Evergreen" / "Anthropic.md"
    entity_note.write_text(
        """---
note_id: anthropic
title: Anthropic
type: evergreen
date: 2026-04-07
---

# Anthropic

Entity and orchestration reference.
""",
        encoding="utf-8",
    )

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="agent-harness",
            title="Agent Harness",
            aliases=[],
            definition="Harness.",
            area="AI",
            kind="concept",
        )
    )
    registry.add_entry(
        ConceptEntry(
            slug="anthropic",
            title="Anthropic",
            aliases=[],
            definition="Company.",
            area="AI",
            kind="entity",
        )
    )
    registry.save()

    rebuild_knowledge_index(temp_vault)

    entity_only_pack = BaseDomainPack(
        name="entity-only",
        version="0.1.0",
        api_version=1,
        _discoverable_object_kinds=["entity"],
    )

    results = discover_related(temp_vault, "entity orchestration reference", limit=5, pack=entity_only_pack)

    assert results
    assert {row["object_kind"] for row in results} == {"entity"}
    assert {row["slug"] for row in results} == {"anthropic"}


def test_pack_evidence_payload_carries_pack_and_object_kind_context(temp_vault):
    from openclaw_pipeline.concept_registry import ConceptEntry, ConceptRegistry
    from openclaw_pipeline.evidence import build_evidence_payload
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
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

Links to [[agent-runtime]].
""",
        encoding="utf-8",
    )

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="agent-harness",
            title="Agent Harness",
            aliases=[],
            definition="Harness.",
            area="AI",
            kind="concept",
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
        query="agent harness",
        mentions=["Agent Harness"],
        slugs=["agent-harness"],
        limit=3,
        pack="default-knowledge",
    )

    assert payload["identity_evidence"][0]["pack"] == "default-knowledge"
    assert payload["identity_evidence"][0]["object_kind"] == "concept"
    assert payload["retrieval_evidence"][0]["pack"] == "default-knowledge"
    assert payload["retrieval_evidence"][0]["object_kind"] == "concept"
