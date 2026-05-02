"""Tests for EntityRegistry — CRUD, alias resolution, persistence, collision detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ovp_pipeline.entity_registry import (
    ENTITY_LAYER_KINDS,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_REJECTED,
    EntityEntry,
    EntityRegistry,
    _normalize_alias,
    is_entity_kind,
    load_entity_registry,
)
from ovp_pipeline.identity import canonicalize_note_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def entity_vault(tmp_path: Path) -> Path:
    """Minimal vault with Entity directory structure."""
    (tmp_path / "10-Knowledge" / "Entity" / "_Candidates").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def registry(entity_vault: Path) -> EntityRegistry:
    """Fresh, empty EntityRegistry."""
    return EntityRegistry(entity_vault).load()


@pytest.fixture()
def seeded_registry(entity_vault: Path) -> EntityRegistry:
    """Registry pre-loaded with 3 seed entities."""
    reg = EntityRegistry(entity_vault)
    reg.load()
    reg.upsert_candidate(
        "andrej-karpathy",
        "Andrej Karpathy",
        "person",
        aliases=["karpathy", "@karpathy"],
        definition="ML researcher; former OpenAI co-founder.",
        confidence=0.95,
    )
    reg.upsert_candidate(
        "claude-code",
        "Claude Code",
        "tool",
        aliases=["claude code"],
        definition="AI coding assistant by Anthropic.",
        confidence=0.9,
    )
    reg.upsert_candidate(
        "anthropic",
        "Anthropic",
        "company",
        aliases=["@anthropicai"],
        definition="AI safety company.",
        confidence=0.88,
    )
    return reg


# ---------------------------------------------------------------------------
# EntityEntry dataclass
# ---------------------------------------------------------------------------

class TestEntityEntry:
    def test_create_valid(self) -> None:
        e = EntityEntry(slug="openai", title="OpenAI", entity_type="company")
        assert e.slug == "openai"
        assert e.entity_type == "company"
        assert e.status == STATUS_CANDIDATE
        assert e.created_at  # auto-set

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid status"):
            EntityEntry(slug="x", title="X", entity_type="tool", status="bogus")

    def test_invalid_entity_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid entity_type"):
            EntityEntry(slug="x", title="X", entity_type="concept")

    def test_legacy_kind_normalized(self) -> None:
        """protocol -> method (legacy mapping), but method is not entity kind."""
        with pytest.raises(ValueError, match="Invalid entity_type"):
            EntityEntry(slug="x", title="X", entity_type="protocol")

    def test_all_surfaces(self) -> None:
        e = EntityEntry(
            slug="andrej-karpathy",
            title="Andrej Karpathy",
            entity_type="person",
            aliases=["karpathy", "@karpathy"],
        )
        surfaces = e.all_surfaces()
        assert "Andrej Karpathy" in surfaces
        assert "karpathy" in surfaces
        assert "@karpathy" in surfaces

    def test_all_surfaces_includes_slug_when_distinct(self) -> None:
        e = EntityEntry(
            slug="ak-researcher",
            title="Andrej Karpathy",
            entity_type="person",
        )
        surfaces = e.all_surfaces()
        assert "Andrej Karpathy" in surfaces
        assert "ak researcher" in surfaces

    def test_roundtrip_dict(self) -> None:
        e = EntityEntry(
            slug="pytorch",
            title="PyTorch",
            entity_type="tool",
            aliases=["torch"],
            definition="DL framework",
        )
        d = e.to_dict()
        e2 = EntityEntry.from_dict(d)
        assert e2.slug == e.slug
        assert e2.entity_type == e.entity_type
        assert e2.aliases == e.aliases

    def test_touch_updates_timestamp(self) -> None:
        e = EntityEntry(slug="x", title="X", entity_type="tool", updated_at="2020-01-01")
        old = e.updated_at
        e.touch()
        assert e.updated_at >= old


# ---------------------------------------------------------------------------
# is_entity_kind helper
# ---------------------------------------------------------------------------

class TestIsEntityKind:
    def test_person_is_entity(self) -> None:
        assert is_entity_kind("person") is True

    def test_concept_is_not_entity(self) -> None:
        assert is_entity_kind("concept") is False

    def test_framework_is_not_entity(self) -> None:
        assert is_entity_kind("framework") is False

    def test_all_entity_kinds(self) -> None:
        for kind in ENTITY_LAYER_KINDS:
            assert is_entity_kind(kind) is True


# ---------------------------------------------------------------------------
# EntityRegistry — CRUD
# ---------------------------------------------------------------------------

class TestRegistryCRUD:
    def test_empty_registry(self, registry: EntityRegistry) -> None:
        assert len(registry) == 0
        assert registry.entries == []

    def test_upsert_candidate_creates(self, registry: EntityRegistry) -> None:
        entry = registry.upsert_candidate("openai", "OpenAI", "company")
        assert entry.slug == "openai"
        assert entry.status == STATUS_CANDIDATE
        assert len(registry) == 1

    def test_upsert_candidate_slug_canonicalized(self, registry: EntityRegistry) -> None:
        entry = registry.upsert_candidate("Claude Code (Anthropic)", "Claude Code", "tool")
        assert entry.slug == "claude-code-anthropic"

    def test_upsert_existing_increments_count(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("openai", "OpenAI", "company")
        entry = registry.upsert_candidate("openai", "OpenAI", "company")
        assert entry.mentioned_in_count == 2

    def test_upsert_merges_aliases(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("openai", "OpenAI", "company", aliases=["oai"])
        registry.upsert_candidate("openai", "OpenAI", "company", aliases=["openai-inc"])
        entry = registry.find_by_slug("openai")
        assert entry is not None
        assert "oai" in entry.aliases
        assert "openai-inc" in entry.aliases

    def test_upsert_rejected_raises(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("openai", "OpenAI", "company")
        registry.reject("openai")
        with pytest.raises(ValueError, match="rejected"):
            registry.upsert_candidate("openai", "OpenAI", "company")

    def test_promote_to_active(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("openai", "OpenAI", "company")
        entry = registry.promote_to_active("openai")
        assert entry.status == STATUS_ACTIVE

    def test_promote_non_candidate_raises(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("openai", "OpenAI", "company")
        registry.promote_to_active("openai")
        with pytest.raises(ValueError, match="not a candidate"):
            registry.promote_to_active("openai")

    def test_promote_nonexistent_raises(self, registry: EntityRegistry) -> None:
        with pytest.raises(ValueError, match="not found"):
            registry.promote_to_active("nonexistent")

    def test_reject(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("openai", "OpenAI", "company")
        entry = registry.reject("openai")
        assert entry.status == STATUS_REJECTED

    def test_reject_nonexistent_raises(self, registry: EntityRegistry) -> None:
        with pytest.raises(ValueError, match="not found"):
            registry.reject("nope")

    def test_merge_entity(self, seeded_registry: EntityRegistry) -> None:
        reg = seeded_registry
        reg.upsert_candidate("karpathy-ai", "Karpathy AI", "person")
        target = reg.merge_entity("karpathy-ai", "andrej-karpathy")
        assert target.slug == "andrej-karpathy"
        assert "Karpathy AI" in target.aliases
        assert reg.find_by_slug("karpathy-ai") is None

    def test_merge_nonexistent_source_raises(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("openai", "OpenAI", "company")
        with pytest.raises(ValueError, match="not found"):
            registry.merge_entity("nope", "openai")

    def test_add_alias(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("openai", "OpenAI", "company")
        registry.add_alias("openai", "OAI")
        entry = registry.find_by_slug("openai")
        assert entry is not None
        assert "OAI" in entry.aliases


# ---------------------------------------------------------------------------
# EntityRegistry — Query / Resolution
# ---------------------------------------------------------------------------

class TestRegistryQuery:
    def test_find_by_slug(self, seeded_registry: EntityRegistry) -> None:
        entry = seeded_registry.find_by_slug("anthropic")
        assert entry is not None
        assert entry.entity_type == "company"

    def test_find_by_alias(self, seeded_registry: EntityRegistry) -> None:
        entry = seeded_registry.find_by_alias("karpathy")
        assert entry is not None
        assert entry.slug == "andrej-karpathy"

    def test_find_by_alias_case_insensitive(self, seeded_registry: EntityRegistry) -> None:
        entry = seeded_registry.find_by_alias("KARPATHY")
        assert entry is not None
        assert entry.slug == "andrej-karpathy"

    def test_resolve_mention_by_slug(self, seeded_registry: EntityRegistry) -> None:
        entry = seeded_registry.resolve_mention("anthropic")
        assert entry is not None
        assert entry.slug == "anthropic"

    def test_resolve_mention_by_alias(self, seeded_registry: EntityRegistry) -> None:
        entry = seeded_registry.resolve_mention("@anthropicai")
        assert entry is not None
        assert entry.slug == "anthropic"

    def test_resolve_mention_returns_none(self, seeded_registry: EntityRegistry) -> None:
        assert seeded_registry.resolve_mention("nonexistent") is None

    def test_resolve_rejected_returns_none(self, seeded_registry: EntityRegistry) -> None:
        seeded_registry.reject("anthropic")
        assert seeded_registry.resolve_mention("anthropic") is None
        assert seeded_registry.resolve_mention("@anthropicai") is None

    def test_has_slug(self, seeded_registry: EntityRegistry) -> None:
        assert seeded_registry.has_slug("claude-code") is True
        assert seeded_registry.has_slug("nope") is False

    def test_count_by_type(self, seeded_registry: EntityRegistry) -> None:
        counts = seeded_registry.count_by_type()
        assert counts["person"] == 1
        assert counts["tool"] == 1
        assert counts["company"] == 1

    def test_find_by_type(self, seeded_registry: EntityRegistry) -> None:
        tools = seeded_registry.find_by_type("tool")
        assert len(tools) == 1
        assert tools[0].slug == "claude-code"

    def test_top_mentioned(self, seeded_registry: EntityRegistry) -> None:
        seeded_registry.upsert_candidate("claude-code", "Claude Code", "tool")
        seeded_registry.upsert_candidate("claude-code", "Claude Code", "tool")
        top = seeded_registry.top_mentioned(n=1)
        assert len(top) == 1
        assert top[0].slug == "claude-code"
        assert top[0].mentioned_in_count == 3

    def test_active_entities(self, seeded_registry: EntityRegistry) -> None:
        assert len(seeded_registry.active_entities) == 0
        seeded_registry.promote_to_active("anthropic")
        assert len(seeded_registry.active_entities) == 1

    def test_candidates(self, seeded_registry: EntityRegistry) -> None:
        assert len(seeded_registry.candidates) == 3


# ---------------------------------------------------------------------------
# Persistence — save / load round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_reload(self, seeded_registry: EntityRegistry) -> None:
        seeded_registry.save()
        reg2 = EntityRegistry(seeded_registry.vault_dir).load()
        assert len(reg2) == 3
        entry = reg2.find_by_slug("andrej-karpathy")
        assert entry is not None
        assert entry.entity_type == "person"
        assert "karpathy" in entry.aliases

    def test_alias_index_file_written(self, seeded_registry: EntityRegistry) -> None:
        seeded_registry.save()
        path = seeded_registry.alias_index_path
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["version"] == 1
        assert "karpathy" in data["aliases"]
        assert data["aliases"]["karpathy"] == "andrej-karpathy"

    def test_registry_jsonl_format(self, seeded_registry: EntityRegistry) -> None:
        seeded_registry.save()
        lines = seeded_registry.registry_path.read_text().strip().split("\n")
        assert len(lines) == 3
        first = json.loads(lines[0])
        assert "slug" in first
        assert "entity_type" in first

    def test_load_entity_registry_helper(self, seeded_registry: EntityRegistry) -> None:
        seeded_registry.save()
        reg = load_entity_registry(seeded_registry.vault_dir)
        assert len(reg) == 3

    def test_empty_save_creates_dirs(self, entity_vault: Path) -> None:
        import shutil
        entity_dir = entity_vault / "10-Knowledge" / "Entity"
        shutil.rmtree(entity_dir)
        reg = EntityRegistry(entity_vault)
        reg.save()
        assert reg.registry_path.exists()
        assert reg.candidates_dir.exists()


# ---------------------------------------------------------------------------
# Alias collision detection
# ---------------------------------------------------------------------------

class TestCollisionDetection:
    def test_no_collisions(self, seeded_registry: EntityRegistry) -> None:
        collisions = seeded_registry.find_alias_collisions()
        assert len(collisions) == 0

    def test_detects_collision(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("claude-code-tool", "Claude Code", "tool")
        registry.upsert_candidate("claude-code-project", "Claude Code", "project")
        collisions = registry.find_alias_collisions()
        assert len(collisions) > 0
        colliding_slugs = set()
        for slugs in collisions.values():
            colliding_slugs.update(slugs)
        assert "claude-code-tool" in colliding_slugs
        assert "claude-code-project" in colliding_slugs


# ---------------------------------------------------------------------------
# Normalize alias
# ---------------------------------------------------------------------------

class TestNormalizeAlias:
    def test_lowercase(self) -> None:
        assert _normalize_alias("Karpathy") == "karpathy"

    def test_strips(self) -> None:
        assert _normalize_alias("  foo  ") == "foo"

    def test_collapses_whitespace(self) -> None:
        assert _normalize_alias("claude   code") == "claude code"

    def test_preserves_at(self) -> None:
        assert _normalize_alias("@karpathy") == "@karpathy"


# ---------------------------------------------------------------------------
# Slug canonicalization integration
# ---------------------------------------------------------------------------

class TestSlugCanonicalization:
    def test_slug_canonicalized_on_upsert(self, registry: EntityRegistry) -> None:
        entry = registry.upsert_candidate("Andrej Karpathy", "Andrej Karpathy", "person")
        assert entry.slug == canonicalize_note_id("Andrej Karpathy")
        assert entry.slug == "andrej-karpathy"

    def test_special_chars_stripped(self, registry: EntityRegistry) -> None:
        entry = registry.upsert_candidate("C++ (Language)", "C++", "tool")
        assert entry.slug == canonicalize_note_id("C++ (Language)")

    def test_consistent_lookup_after_canonicalization(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("OpenAI Inc.", "OpenAI", "company")
        slug = canonicalize_note_id("OpenAI Inc.")
        assert registry.find_by_slug(slug) is not None


# ---------------------------------------------------------------------------
# Confidence averaging
# ---------------------------------------------------------------------------

class TestConfidenceAvg:
    def test_initial_confidence(self, registry: EntityRegistry) -> None:
        entry = registry.upsert_candidate("x", "X", "tool", confidence=0.9)
        assert entry.confidence_avg == pytest.approx(0.9)

    def test_running_average(self, registry: EntityRegistry) -> None:
        registry.upsert_candidate("x", "X", "tool", confidence=0.8)
        entry = registry.upsert_candidate("x", "X", "tool", confidence=1.0)
        assert entry.confidence_avg == pytest.approx(0.9, abs=0.05)
