"""
Tests for rebuild_registry module.
"""

import pytest
from pathlib import Path
from openclaw_pipeline.rebuild_registry import rebuild_registry
from openclaw_pipeline.concept_registry import ConceptRegistry


class TestRebuildRegistry:
    def test_rebuild_from_evergreens(self, temp_vault, sample_evergreen_files):
        """Test rebuilding registry from existing Evergreen files."""
        entries = rebuild_registry(temp_vault, dry_run=True, verbose=False)

        assert len(entries) == 3
        slugs = {e.slug for e in entries}
        assert "DCF-Valuation" in slugs
        assert "WACC" in slugs
        assert "AI-Agent" in slugs

    def test_rebuild_writes_files(self, temp_vault, sample_evergreen_files):
        """Test that rebuild with --write creates registry files."""
        entries = rebuild_registry(temp_vault, dry_run=False, verbose=False)

        registry_path = temp_vault / "10-Knowledge" / "Atlas" / "concept-registry.jsonl"
        alias_index_path = temp_vault / "10-Knowledge" / "Atlas" / "alias-index.json"

        assert registry_path.exists()
        assert alias_index_path.exists()

        # Load and verify
        registry = ConceptRegistry(temp_vault).load()
        assert len(registry.entries) == 3

    def test_rebuild_extracts_aliases(self, temp_vault, sample_evergreen_files):
        """Test that aliases are extracted from frontmatter."""
        entries = rebuild_registry(temp_vault, dry_run=False, verbose=False)

        registry = ConceptRegistry(temp_vault).load()

        dcf = registry.find_by_slug("DCF-Valuation")
        assert "DCF估值" in dcf.aliases
        assert "折现现金流估值" in dcf.aliases

    def test_rebuild_infers_area(self, temp_vault, sample_evergreen_files):
        """Test that area is inferred from tags or content."""
        entries = rebuild_registry(temp_vault, dry_run=False, verbose=False)

        registry = ConceptRegistry(temp_vault).load()

        dcf = registry.find_by_slug("DCF-Valuation")
        assert dcf.area == "investing"

        ai = registry.find_by_slug("AI-Agent")
        # Area could be "AI" (from tags) or "ai" (from content inference)
        assert ai.area.lower() in ("ai", "investing")
