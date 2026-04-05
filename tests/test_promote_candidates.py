"""
Tests for promote_candidates module.
"""

import pytest
from pathlib import Path
from openclaw_pipeline.concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
)


class TestCandidatePromotion:
    def test_promote_to_active(self, temp_vault):
        """Test promoting a candidate to active."""
        registry = ConceptRegistry(temp_vault)
        registry.upsert_candidate(
            slug="new-concept",
            title="New Concept",
            definition="A new concept.",
            area="testing",
        )
        registry.save()

        # Verify it's a candidate
        entry = registry.find_by_slug("new-concept")
        assert entry.status == STATUS_CANDIDATE

        # Promote
        registry.promote_to_active("new-concept")
        registry.save()

        # Verify it's now active
        entry = registry.find_by_slug("new-concept")
        assert entry.status == STATUS_ACTIVE

    def test_merge_as_alias(self, temp_vault):
        """Test merging a candidate as an alias."""
        registry = ConceptRegistry(temp_vault)

        # Create active concept
        registry.add_entry(ConceptEntry(
            slug="existing-concept",
            title="Existing Concept",
            aliases=["old-alias"],
            definition="Existing.",
            area="testing",
        ))

        # Create candidate to merge
        registry.upsert_candidate(
            slug="variant-name",
            title="Variant Name",
            definition="A variant.",
            area="testing",
            aliases=["variant-alias"],
        )
        registry.save()

        # Merge
        registry.merge_as_alias(
            "variant-name",
            "existing-concept",
            ["variant-alias", "another-alias"],
        )
        registry.save()

        # Verify variant is removed
        assert registry.find_by_slug("variant-name") is None

        # Verify aliases added to existing
        existing = registry.find_by_slug("existing-concept")
        assert "variant-alias" in existing.aliases
        assert "another-alias" in existing.aliases

    def test_reject_candidate(self, temp_vault):
        """Test rejecting a candidate."""
        registry = ConceptRegistry(temp_vault)
        registry.upsert_candidate(
            slug="reject-me",
            title="Reject Me",
            definition="Reject.",
            area="testing",
        )
        registry.save()

        # Reject
        registry.reject("reject-me")
        registry.save()

        # Verify it's rejected
        entry = registry.find_by_slug("reject-me")
        assert entry.status == "rejected"

    def test_upsert_candidate_increments_count(self, temp_vault):
        """Test that upserting same candidate increments source_count."""
        registry = ConceptRegistry(temp_vault)

        registry.upsert_candidate(
            slug="new-concept",
            title="New Concept",
            definition="First.",
            area="testing",
        )
        registry.save()

        entry1 = registry.find_by_slug("new-concept")
        assert entry1.source_count == 1

        # Upsert again
        registry.upsert_candidate(
            slug="new-concept",
            title="New Concept",
            definition="Second.",
            area="testing",
        )
        registry.save()

        entry2 = registry.find_by_slug("new-concept")
        assert entry2.source_count == 2
