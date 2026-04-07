"""
Tests for promote_candidates module.
"""

import pytest
from pathlib import Path
from types import SimpleNamespace
from openclaw_pipeline.concept_registry import (
    ConceptRegistry,
    ConceptEntry,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
)
from openclaw_pipeline.auto_evergreen_extractor import (
    AutoEvergreenExtractor,
    PipelineLogger as EvergreenLogger,
    main as evergreen_main,
)
from openclaw_pipeline.promote_candidates import (
    merge_candidate,
    promote_candidate,
    reject_candidate,
    write_candidate_file,
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

    def test_promote_candidate_syncs_files_and_atlas(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="new-concept",
            title="New Concept",
            definition="A new concept.",
            area="testing",
        )
        registry.save()
        write_candidate_file(temp_vault, entry, dry_run=False)

        mutation = promote_candidate(temp_vault, "new-concept", dry_run=False)

        evergreen_path = temp_vault / "10-Knowledge" / "Evergreen" / "new-concept.md"
        candidate_path = temp_vault / "10-Knowledge" / "Evergreen" / "_Candidates" / "new-concept.md"
        atlas_index = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"

        assert evergreen_path.exists()
        assert candidate_path.exists() is False
        assert atlas_index.exists()
        assert "[[new-concept|New Concept]]" in atlas_index.read_text(encoding="utf-8")
        assert mutation.atlas_refreshed is True

    def test_merge_candidate_rewrites_links_and_removes_candidate_file(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        registry.add_entry(ConceptEntry(
            slug="existing-concept",
            title="Existing Concept",
            aliases=["existing"],
            definition="Existing.",
            area="testing",
        ))
        candidate = registry.upsert_candidate(
            slug="variant-name",
            title="Variant Name",
            definition="A variant.",
            area="testing",
            aliases=["variant-alias"],
        )
        registry.save()
        write_candidate_file(temp_vault, candidate, dry_run=False)

        article_path = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04-01_Test.md"
        article_path.parent.mkdir(parents=True, exist_ok=True)
        article_path.write_text(
            "# Test\n\nLinks [[Variant Name]] and [[variant-alias]].\n",
            encoding="utf-8",
        )

        mutation = merge_candidate(temp_vault, "variant-name", "existing-concept", dry_run=False)

        updated = article_path.read_text(encoding="utf-8")
        existing = ConceptRegistry(temp_vault).load().find_by_slug("existing-concept")
        candidate_path = temp_vault / "10-Knowledge" / "Evergreen" / "_Candidates" / "variant-name.md"

        assert candidate_path.exists() is False
        assert "[[existing-concept|Variant Name]]" in updated
        assert "[[existing-concept|variant-alias]]" in updated
        assert "variant-name" in existing.redirects
        assert mutation.link_updates[str(article_path)] == 2

    def test_reject_candidate_removes_candidate_file_and_updates_atlas(self, temp_vault):
        registry = ConceptRegistry(temp_vault)
        entry = registry.upsert_candidate(
            slug="reject-me",
            title="Reject Me",
            definition="Reject.",
            area="testing",
        )
        registry.save()
        write_candidate_file(temp_vault, entry, dry_run=False)

        mutation = reject_candidate(temp_vault, "reject-me", dry_run=False)

        candidate_path = temp_vault / "10-Knowledge" / "Evergreen" / "_Candidates" / "reject-me.md"
        atlas_index = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
        reloaded = ConceptRegistry(temp_vault).load().find_by_slug("reject-me")

        assert candidate_path.exists() is False
        assert atlas_index.exists()
        assert reloaded.status == "rejected"
        assert mutation.atlas_refreshed is True

    def test_auto_evergreen_extractor_writes_candidate_file(self, temp_vault):
        logger = EvergreenLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
        extractor = AutoEvergreenExtractor(temp_vault, logger)
        extractor.extractor = SimpleNamespace(
            extract_concepts=lambda file_path, content: [{
                "concept_name": "new-candidate",
                "title": "New Candidate",
                "one_sentence_def": "Candidate definition",
            }]
        )

        source_file = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04-01_Test_深度解读.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("# Test\n", encoding="utf-8")

        result = extractor.process_file(source_file, dry_run=False)

        candidate_path = temp_vault / "10-Knowledge" / "Evergreen" / "_Candidates" / "new-candidate.md"
        entry = ConceptRegistry(temp_vault).load().find_by_slug("new-candidate")

        assert result["candidates_added"] == 1
        assert candidate_path.exists()
        assert entry is not None
        assert entry.status == STATUS_CANDIDATE

    def test_auto_evergreen_extractor_auto_promotes_into_active_note_with_note_id(self, temp_vault):
        logger = EvergreenLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
        extractor = AutoEvergreenExtractor(temp_vault, logger)
        extractor.extractor = SimpleNamespace(
            extract_concepts=lambda file_path, content: [{
                "concept_name": "promoted-concept",
                "title": "Promoted Concept",
                "one_sentence_def": "Promoted definition",
                "explanation": "Detailed explanation",
                "importance": "Important",
                "related_concepts": ["existing-concept"],
            }]
        )

        registry = ConceptRegistry(temp_vault)
        registry.upsert_candidate(
            slug="promoted-concept",
            title="Promoted Concept",
            definition="Promoted definition",
            area="general",
        )
        registry.upsert_candidate(
            slug="promoted-concept",
            title="Promoted Concept",
            definition="Promoted definition",
            area="general",
        )
        registry.save()

        source_file = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "2026-04-01_Test_深度解读.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("# Test\n", encoding="utf-8")

        result = extractor.process_file(
            source_file,
            dry_run=False,
            auto_promote=True,
            promote_threshold=3,
        )

        evergreen_path = temp_vault / "10-Knowledge" / "Evergreen" / "promoted-concept.md"
        atlas_index = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
        candidate_path = temp_vault / "10-Knowledge" / "Evergreen" / "_Candidates" / "promoted-concept.md"
        entry = ConceptRegistry(temp_vault).load().find_by_slug("promoted-concept")
        content = evergreen_path.read_text(encoding="utf-8")

        assert result["concepts_promoted"] == 1
        assert result["concepts_created"] == 1
        assert evergreen_path.exists()
        assert "note_id: promoted-concept" in content
        assert atlas_index.exists()
        assert "[[promoted-concept|Promoted Concept]]" in atlas_index.read_text(encoding="utf-8")
        assert candidate_path.exists() is False
        assert entry is not None
        assert entry.status == STATUS_ACTIVE

    def test_auto_evergreen_cli_dir_passes_auto_promote(self, monkeypatch, temp_vault):
        captured = {}

        class FakeExtractor:
            DEFAULT_PROMOTE_THRESHOLD = 3

            def __init__(self, vault_dir, logger):
                captured["vault_dir"] = Path(vault_dir)

            def init_llm(self, api_key=None, api_base=None):
                return None

            def process_directory(self, directory, dry_run=False, auto_promote=False, promote_threshold=0):
                captured["directory"] = Path(directory)
                captured["dry_run"] = dry_run
                captured["auto_promote"] = auto_promote
                captured["promote_threshold"] = promote_threshold
                return []

        monkeypatch.setattr("openclaw_pipeline.auto_evergreen_extractor.AutoEvergreenExtractor", FakeExtractor)
        monkeypatch.setattr(
            "sys.argv",
            [
                "ovp-evergreen",
                "--vault-dir", str(temp_vault),
                "--dir", str(temp_vault / "20-Areas" / "AI-Research" / "Topics"),
                "--auto-promote",
                "--promote-threshold", "5",
            ],
        )

        result = evergreen_main()

        assert result == 0
        assert captured["vault_dir"] == temp_vault.resolve()
        assert captured["auto_promote"] is True
        assert captured["promote_threshold"] == 5
