"""
Tests for check_showcase_links module.
"""

import pytest
from pathlib import Path
from openclaw_pipeline.check_showcase_links import (
    RegistryLinkChecker,
    LinkCheckResult,
)


class TestRegistryLinkChecker:
    def test_check_active_slug_ok(self, temp_vault, sample_evergreen_files):
        """Test that active slug returns ok."""
        from openclaw_pipeline.concept_registry import ConceptRegistry, ConceptEntry

        registry = ConceptRegistry(temp_vault)
        registry.add_entry(ConceptEntry(
            slug="dcf-valuation",
            title="DCF Valuation",
            aliases=["DCF估值"],
            definition="DCF.",
            area="investing",
        ))
        registry.save()

        checker = RegistryLinkChecker(temp_vault)
        result = checker.check_link("dcf-valuation")

        assert result.status == "ok"
        assert result.canonical_slug == "dcf-valuation"

    def test_check_alias_warning(self, temp_vault, sample_evergreen_files):
        """Test that alias returns alias_warning."""
        from openclaw_pipeline.concept_registry import ConceptRegistry, ConceptEntry

        registry = ConceptRegistry(temp_vault)
        registry.add_entry(ConceptEntry(
            slug="dcf-valuation",
            title="DCF Valuation",
            aliases=["DCF估值"],
            definition="DCF.",
            area="investing",
        ))
        registry.save()

        checker = RegistryLinkChecker(temp_vault)
        result = checker.check_link("DCF估值")

        assert result.status == "alias_warning"
        assert result.canonical_slug == "dcf-valuation"
        assert "dcf-valuation" in result.suggestion

    def test_check_broken_link(self, temp_vault, sample_evergreen_files):
        """Test that nonexistent link returns broken."""
        from openclaw_pipeline.concept_registry import ConceptRegistry

        registry = ConceptRegistry(temp_vault)
        registry.save()

        checker = RegistryLinkChecker(temp_vault)
        result = checker.check_link("nonexistent-concept")

        assert result.status == "broken"

    def test_check_file_exists_ok(self, temp_vault):
        """Test that link to existing file returns ok."""
        from openclaw_pipeline.concept_registry import ConceptRegistry

        # Create a file
        (temp_vault / "10-Knowledge" / "Evergreen" / "Test.md").touch()

        registry = ConceptRegistry(temp_vault)
        registry.save()

        checker = RegistryLinkChecker(temp_vault)
        result = checker.check_link("Test")

        assert result.status == "ok"


class TestLinkCheckResult:
    def test_result_fields(self):
        result = LinkCheckResult(
            file_path="test.md",
            surface="DCF估值",
            status="alias_warning",
            canonical_slug="dcf-valuation",
            suggestion="Use canonical slug",
        )

        assert result.surface == "DCF估值"
        assert result.status == "alias_warning"
        assert result.canonical_slug == "dcf-valuation"
