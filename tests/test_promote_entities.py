"""Tests for promote_entities — promote/merge/reject Entity candidates."""

from __future__ import annotations

from pathlib import Path

import pytest

from ovp_pipeline.entity_registry import (
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_REJECTED,
    EntityRegistry,
)
from ovp_pipeline.promote_entities import (
    AUTO_PROMOTE_CONFIDENCE,
    AUTO_PROMOTE_THRESHOLD,
    EntityMutation,
    auto_promote_all,
    auto_promote_eligible,
    merge_entity,
    promote_entity,
    reject_entity,
    write_candidate_file,
    write_entity_file,
    _replace_wikilinks_in_text,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def entity_vault(tmp_path: Path) -> Path:
    (tmp_path / "10-Knowledge" / "Entity" / "_Candidates").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def seeded_vault(entity_vault: Path) -> Path:
    reg = EntityRegistry(entity_vault)
    reg.load()
    reg.upsert_candidate(
        "andrej-karpathy", "Andrej Karpathy", "person",
        aliases=["karpathy"],
        definition="ML researcher.",
        confidence=0.95,
    )
    reg.upsert_candidate(
        "openai", "OpenAI", "company",
        definition="AI company.",
        confidence=0.9,
    )
    reg.save()
    return entity_vault


# ---------------------------------------------------------------------------
# Write candidate / entity file
# ---------------------------------------------------------------------------

class TestWriteFiles:
    def test_write_candidate_file(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        path = write_candidate_file(seeded_vault, entry, dry_run=False)
        assert path is not None
        assert path.exists()
        text = path.read_text()
        assert "entity_type: company" in text
        assert "OpenAI" in text

    def test_write_candidate_idempotent(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        write_candidate_file(seeded_vault, entry, dry_run=False)
        path2 = write_candidate_file(seeded_vault, entry, dry_run=False)
        assert path2 is not None

    def test_write_entity_file(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        entry.status = STATUS_ACTIVE  # simulate promotion
        path = write_entity_file(seeded_vault, entry, dry_run=False)
        assert path is not None
        assert path.exists()
        text = path.read_text()
        assert "type: entity" in text
        assert "entity_type: company" in text

    def test_dry_run_returns_none(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        assert write_entity_file(seeded_vault, entry, dry_run=True) is None
        assert write_candidate_file(seeded_vault, entry, dry_run=True) is None


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------

class TestPromote:
    def test_promote_creates_file(self, seeded_vault: Path) -> None:
        mutation = promote_entity(seeded_vault, "openai", dry_run=False)
        assert mutation.action == "promote"
        assert len(mutation.touched_files) == 1
        entity_path = seeded_vault / "10-Knowledge" / "Entity" / "openai.md"
        assert entity_path.exists()

    def test_promote_updates_registry(self, seeded_vault: Path) -> None:
        promote_entity(seeded_vault, "openai", dry_run=False)
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        assert entry.status == STATUS_ACTIVE

    def test_promote_deletes_candidate(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        write_candidate_file(seeded_vault, reg.find_by_slug("openai"), dry_run=False)
        mutation = promote_entity(seeded_vault, "openai", dry_run=False)
        assert len(mutation.deleted_files) == 1
        candidate = seeded_vault / "10-Knowledge" / "Entity" / "_Candidates" / "openai.md"
        assert not candidate.exists()

    def test_promote_nonexistent_raises(self, seeded_vault: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            promote_entity(seeded_vault, "nope", dry_run=False)

    def test_promote_non_candidate_raises(self, seeded_vault: Path) -> None:
        promote_entity(seeded_vault, "openai", dry_run=False)
        with pytest.raises(ValueError, match="not a candidate"):
            promote_entity(seeded_vault, "openai", dry_run=False)

    def test_promote_dry_run(self, seeded_vault: Path) -> None:
        mutation = promote_entity(seeded_vault, "openai", dry_run=True)
        assert mutation.action == "promote"
        entity_path = seeded_vault / "10-Knowledge" / "Entity" / "openai.md"
        assert not entity_path.exists()


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------

class TestReject:
    def test_reject_updates_registry(self, seeded_vault: Path) -> None:
        reject_entity(seeded_vault, "openai", dry_run=False)
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        assert entry.status == STATUS_REJECTED

    def test_reject_deletes_candidate_file(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        write_candidate_file(seeded_vault, reg.find_by_slug("openai"), dry_run=False)
        mutation = reject_entity(seeded_vault, "openai", dry_run=False)
        assert len(mutation.deleted_files) == 1

    def test_reject_nonexistent_raises(self, seeded_vault: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            reject_entity(seeded_vault, "nope", dry_run=False)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

class TestMerge:
    def test_merge_transfers_aliases(self, seeded_vault: Path) -> None:
        merge_entity(seeded_vault, "andrej-karpathy", "openai", dry_run=False)
        reg = EntityRegistry(seeded_vault).load()
        target = reg.find_by_slug("openai")
        assert "Andrej Karpathy" in target.aliases or "karpathy" in target.aliases
        assert reg.find_by_slug("andrej-karpathy") is None

    def test_merge_rewrites_wikilinks(self, seeded_vault: Path) -> None:
        md_file = seeded_vault / "test.md"
        md_file.write_text("See [[andrej-karpathy]] for info.", encoding="utf-8")
        merge_entity(seeded_vault, "andrej-karpathy", "openai", dry_run=False)
        text = md_file.read_text()
        assert "[[openai]]" in text
        assert "[[andrej-karpathy]]" not in text


# ---------------------------------------------------------------------------
# Auto-promote
# ---------------------------------------------------------------------------

class TestAutoPromote:
    def test_auto_promote_eligible_true(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        entry.mentioned_in_count = AUTO_PROMOTE_THRESHOLD
        entry.confidence_avg = AUTO_PROMOTE_CONFIDENCE
        assert auto_promote_eligible(entry) is True

    def test_auto_promote_eligible_low_count(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        entry.mentioned_in_count = 1
        entry.confidence_avg = 0.95
        assert auto_promote_eligible(entry) is False

    def test_auto_promote_eligible_low_confidence(self, seeded_vault: Path) -> None:
        reg = EntityRegistry(seeded_vault).load()
        entry = reg.find_by_slug("openai")
        entry.mentioned_in_count = 5
        entry.confidence_avg = 0.5
        assert auto_promote_eligible(entry) is False

    def test_auto_promote_all(self, entity_vault: Path) -> None:
        reg = EntityRegistry(entity_vault).load()
        for i in range(AUTO_PROMOTE_THRESHOLD):
            reg.upsert_candidate("test-entity", "Test Entity", "tool", confidence=0.9)
        reg.save()
        mutations = auto_promote_all(entity_vault, dry_run=False)
        assert len(mutations) == 1
        assert mutations[0].action == "promote"


# ---------------------------------------------------------------------------
# Wikilink rewriting
# ---------------------------------------------------------------------------

class TestWikilinkRewrite:
    def test_simple_replace(self) -> None:
        text = "See [[old-slug]] for details."
        new_text, count = _replace_wikilinks_in_text(text, "old-slug", "new-slug")
        assert "[[new-slug]]" in new_text
        assert count == 1

    def test_aliased_link_preserved(self) -> None:
        text = "See [[old-slug|Old Name]] for info."
        new_text, count = _replace_wikilinks_in_text(text, "old-slug", "new-slug")
        assert "[[new-slug|Old Name]]" in new_text
        assert count == 1

    def test_no_match(self) -> None:
        text = "See [[other-slug]] for details."
        new_text, count = _replace_wikilinks_in_text(text, "old-slug", "new-slug")
        assert new_text == text
        assert count == 0

    def test_multiple_replacements(self) -> None:
        text = "A [[old-slug]] B [[old-slug|x]] C [[other]]"
        new_text, count = _replace_wikilinks_in_text(text, "old-slug", "new-slug")
        assert count == 2
        assert "[[new-slug]]" in new_text
        assert "[[new-slug|x]]" in new_text
        assert "[[other]]" in new_text


# ---------------------------------------------------------------------------
# EntityMutation
# ---------------------------------------------------------------------------

class TestEntityMutation:
    def test_to_dict(self) -> None:
        m = EntityMutation(action="promote", slug="x", touched_files=["a.md"])
        d = m.to_dict()
        assert d["action"] == "promote"
        assert d["touched_files"] == ["a.md"]
