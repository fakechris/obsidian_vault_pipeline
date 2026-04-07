"""
Tests for concept_registry module.
"""

import pytest
from pathlib import Path
from openclaw_pipeline.concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
)


class TestConceptEntry:
    def test_create_entry(self):
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=["alias1", "alias2"],
            definition="A test concept.",
            area="testing",
            status=STATUS_ACTIVE,
        )
        assert entry.slug == "test-concept"
        assert entry.title == "Test Concept"
        assert len(entry.aliases) == 2
        assert entry.status == STATUS_ACTIVE
        assert entry.last_seen_at != ""

    def test_entry_to_dict(self):
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=["alias1"],
            definition="A test concept.",
            area="testing",
        )
        d = entry.to_dict()
        assert d["slug"] == "test-concept"
        assert d["title"] == "Test Concept"
        assert "alias1" in d["aliases"]

    def test_entry_from_dict(self):
        data = {
            "slug": "test-concept",
            "title": "Test Concept",
            "aliases": ["alias1"],
            "definition": "A test concept.",
            "area": "testing",
            "status": STATUS_ACTIVE,
        }
        entry = ConceptEntry.from_dict(data)
        assert entry.slug == "test-concept"
        assert entry.aliases == ["alias1"]


class TestConceptRegistry:
    def test_create_registry(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        assert registry.vault_dir == temp_vault
        assert len(registry.entries) == 0

    def test_load_empty_registry(self, temp_vault):
        registry = ConceptRegistry(temp_vault).load()
        assert len(registry.entries) == 0

    def test_add_entry(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=[],
            definition="A test concept.",
            area="testing",
        )
        registry.add_entry(entry)
        assert len(registry.entries) == 1
        assert registry.find_by_slug("test-concept").slug == "test-concept"

    def test_add_duplicate_entry_raises(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=[],
            definition="A test concept.",
            area="testing",
        )
        registry.add_entry(entry)
        with pytest.raises(ValueError):
            registry.add_entry(entry)

    def test_upsert_entry(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=[],
            definition="A test concept.",
            area="testing",
        )
        registry.upsert_entry(entry)
        assert len(registry.entries) == 1

        # Upsert again should update
        entry.title = "Updated Title"
        registry.upsert_entry(entry)
        assert len(registry.entries) == 1
        assert registry.find_by_slug("test-concept").title == "Updated Title"

    def test_find_by_slug(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=[],
            definition="A test concept.",
            area="testing",
        )
        registry.add_entry(entry)

        found = registry.find_by_slug("test-concept")
        assert found is not None
        assert found.slug == "test-concept"

        not_found = registry.find_by_slug("nonexistent")
        assert not_found is None

    def test_find_by_alias(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=["alias1", "Alias2"],
            definition="A test concept.",
            area="testing",
        )
        registry.add_entry(entry)

        # Alias match is case-insensitive
        found = registry.find_by_alias("alias1")
        assert found is not None
        assert found.slug == "test-concept"

        found = registry.find_by_alias("ALIAS1")
        assert found is not None

        not_found = registry.find_by_alias("nonexistent")
        assert not_found is None

    def test_has_active_slug(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="active-concept",
            title="Active Concept",
            aliases=[],
            definition="An active concept.",
            area="testing",
            status=STATUS_ACTIVE,
        )
        registry.add_entry(entry)

        assert registry.has_active_slug("active-concept") is True
        assert registry.has_active_slug("nonexistent") is False

    def test_has_alias(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=["alias1"],
            definition="A test concept.",
            area="testing",
        )
        registry.add_entry(entry)

        assert registry.has_alias("alias1") is True
        assert registry.has_alias("nonexistent") is False

    def test_search(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entries = [
            ConceptEntry(
                slug="dcf-valuation",
                title="DCF Valuation",
                aliases=["DCF估值"],
                definition="折现现金流估值方法。",
                area="investing",
            ),
            ConceptEntry(
                slug="wacc",
                title="WACC",
                aliases=["加权平均资本成本"],
                definition="加权平均资本成本。",
                area="investing",
            ),
            ConceptEntry(
                slug="ai-agent",
                title="AI Agent",
                aliases=["AI代理"],
                definition="AI代理系统。",
                area="AI",
            ),
        ]
        for e in entries:
            registry.add_entry(e)

        # Search for DCF
        results = registry.search("DCF", topk=5)
        assert len(results) > 0
        assert results[0][0].slug == "dcf-valuation"

        # Search for investing
        results = registry.search("valuation", area="investing", topk=5)
        assert all(r[0].area == "investing" for r in results)

    def test_search_falls_back_to_alias_surface_ranking(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        registry.add_entry(
            ConceptEntry(
                slug="agent-harness",
                title="Agent Harness",
                aliases=["AI agent harness"],
                definition="Harness for running agents.",
                area="AI",
            )
        )
        registry.add_entry(
            ConceptEntry(
                slug="agent-runtime",
                title="Agent Runtime",
                aliases=["runtime orchestration"],
                definition="Runtime for orchestration.",
                area="AI",
            )
        )

        results = registry.search("agent harness", topk=5)
        assert results
        assert results[0][0].slug == "agent-harness"
        assert results[0][1] >= 0.9

    def test_persist_and_load(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=["alias1"],
            definition="A test concept.",
            area="testing",
        )
        registry.add_entry(entry)
        registry.save()

        # Load in new registry
        registry2 = ConceptRegistry(temp_vault).load()
        assert len(registry2.entries) == 1
        assert registry2.find_by_slug("test-concept").title == "Test Concept"

    def test_active_concepts_property(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        registry.add_entry(ConceptEntry(
            slug="active-concept",
            title="Active",
            aliases=[],
            definition="Active.",
            area="test",
            status=STATUS_ACTIVE,
        ))
        registry.add_entry(ConceptEntry(
            slug="candidate-concept",
            title="Candidate",
            aliases=[],
            definition="Candidate.",
            area="test",
            status=STATUS_CANDIDATE,
        ))

        assert len(registry.active_concepts) == 1
        assert len(registry.candidates) == 1

    def test_normalize_surface(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = ConceptEntry(
            slug="test-concept",
            title="Test Concept",
            aliases=["Alias1"],
            definition="Test.",
            area="test",
        )
        registry.add_entry(entry)

        # Should find by normalized alias
        found = registry.find_by_alias("ALIAS1")
        assert found is not None
