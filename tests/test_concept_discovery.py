from __future__ import annotations


def test_resolve_mention_uses_shared_discovery_related_context(temp_vault, monkeypatch):
    from openclaw_pipeline import concept_registry as registry_module
    from openclaw_pipeline.concept_registry import ConceptRegistry, ResolutionAction

    def fake_discover(vault_dir, query, engine="knowledge", limit=5):  # noqa: ARG001
        return [
            {
                "engine": engine,
                "kind": "semantic",
                "slug": "agent-runtime",
                "title": "Agent Runtime",
                "score": 0.88,
                "snippet": "runtime orchestration",
            }
        ]

    monkeypatch.setattr(registry_module, "discover_related", fake_discover)

    registry = ConceptRegistry(temp_vault)
    result = registry.resolve_mention("Unknown Runtime Concept")

    assert result.action == ResolutionAction.CREATE_CANDIDATE
    assert len(result.related_context) == 1
    assert result.related_context[0].slug == "agent-runtime"
    assert result.related_context[0].engine == "knowledge"
    assert result.related_context[0].kind == "semantic"


def test_fix_surface_conflicts_uses_similarity_as_review_signal_only(temp_vault, monkeypatch):
    from openclaw_pipeline.concept_registry import ConceptEntry, ConceptRegistry

    registry = ConceptRegistry(temp_vault)
    registry.add_entry(
        ConceptEntry(
            slug="agent-harness",
            title="Agent Harness",
            aliases=["Agent Runtime"],
            definition="Harness.",
            area="AI",
        )
    )
    registry.add_entry(
        ConceptEntry(
            slug="agent-runtime",
            title="Agent Runtime",
            aliases=["Agent Runtime"],
            definition="Runtime.",
            area="AI",
        )
    )

    monkeypatch.setattr(ConceptRegistry, "get_bidirectional_similarity", lambda self, t1, s1, t2, s2: 0.95)

    results = registry.fix_surface_conflicts(dry_run=True)

    assert results["merge_candidates"] == []
    assert results["review_needed"]
    assert results["review_needed"][0]["action"] == "review"
