"""Tests for EntityExtractor — LLM NER mock + alias matching + resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ovp_pipeline.entity_extractor import (
    CONFIDENCE_THRESHOLD,
    EntityExtractor,
    EntityMention,
    ExtractionResult,
    make_extractor,
)
from ovp_pipeline.entity_registry import EntityRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def entity_vault(tmp_path: Path) -> Path:
    (tmp_path / "10-Knowledge" / "Entity" / "_Candidates").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def registry(entity_vault: Path) -> EntityRegistry:
    return EntityRegistry(entity_vault).load()


@pytest.fixture()
def seeded_registry(entity_vault: Path) -> EntityRegistry:
    reg = EntityRegistry(entity_vault).load()
    reg.upsert_candidate(
        "andrej-karpathy", "Andrej Karpathy", "person",
        aliases=["karpathy", "@karpathy"],
        confidence=0.95,
    )
    reg.upsert_candidate(
        "pytorch", "PyTorch", "tool",
        aliases=["torch"],
        confidence=0.9,
    )
    reg.upsert_candidate(
        "openai", "OpenAI", "company",
        confidence=0.88,
    )
    return reg


def _make_llm_mock(entities: list[dict]) -> callable:
    """Return a callable that returns a mock LLM response."""
    response = json.dumps(entities, ensure_ascii=False)

    def mock_llm(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        return response

    return mock_llm


# ---------------------------------------------------------------------------
# EntityMention / ExtractionResult data classes
# ---------------------------------------------------------------------------

class TestEntityMention:
    def test_to_dict(self) -> None:
        m = EntityMention(text="OpenAI", kind="company", confidence=0.9)
        d = m.to_dict()
        assert d["text"] == "OpenAI"
        assert d["kind"] == "company"
        assert d["resolution"] == "unresolved"

    def test_default_values(self) -> None:
        m = EntityMention(text="X", kind="tool", confidence=0.5)
        assert m.resolved_slug is None
        assert m.snippet == ""


class TestExtractionResult:
    def test_to_dict(self) -> None:
        r = ExtractionResult(source_file="test.md")
        r.mentions.append(EntityMention(text="A", kind="person", confidence=0.9))
        d = r.to_dict()
        assert d["source_file"] == "test.md"
        assert len(d["mentions"]) == 1


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------

class TestLLMResponseParsing:
    def test_parse_valid_json(self) -> None:
        text = '[{"text": "OpenAI", "kind": "company", "confidence": 0.95, "snippet": "..."}]'
        result = EntityExtractor._parse_llm_response(text)
        assert len(result) == 1
        assert result[0]["text"] == "OpenAI"

    def test_parse_with_markdown_fences(self) -> None:
        text = '```json\n[{"text": "X", "kind": "tool", "confidence": 0.8}]\n```'
        result = EntityExtractor._parse_llm_response(text)
        assert len(result) == 1

    def test_parse_invalid_json(self) -> None:
        result = EntityExtractor._parse_llm_response("not json at all")
        assert result == []

    def test_parse_empty_array(self) -> None:
        result = EntityExtractor._parse_llm_response("[]")
        assert result == []

    def test_parse_with_trailing_text(self) -> None:
        text = 'Here are the entities:\n[{"text": "A", "kind": "person", "confidence": 0.9}]\nDone!'
        result = EntityExtractor._parse_llm_response(text)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Alias resolution — existing entities
# ---------------------------------------------------------------------------

class TestAliasResolution:
    def test_resolve_existing_by_title(self, seeded_registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "Andrej Karpathy", "kind": "person", "confidence": 0.95, "snippet": "..."},
        ])
        ext = EntityExtractor(seeded_registry, llm_call=llm)
        result = ext.extract_entities("Andrej Karpathy is great.", "test.md")
        assert result.existing_matched == 1
        assert result.mentions[0].resolution == "alias_hit"
        assert result.mentions[0].resolved_slug == "andrej-karpathy"

    def test_resolve_existing_by_alias(self, seeded_registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "karpathy", "kind": "person", "confidence": 0.9, "snippet": "..."},
        ])
        ext = EntityExtractor(seeded_registry, llm_call=llm)
        result = ext.extract_entities("karpathy's blog", "test.md")
        assert result.existing_matched == 1
        assert result.mentions[0].resolved_slug == "andrej-karpathy"

    def test_resolve_existing_by_slug(self, seeded_registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "pytorch", "kind": "tool", "confidence": 0.85, "snippet": "..."},
        ])
        ext = EntityExtractor(seeded_registry, llm_call=llm)
        result = ext.extract_entities("Use pytorch.", "test.md")
        assert result.existing_matched == 1

    def test_mentioned_count_incremented(self, seeded_registry: EntityRegistry) -> None:
        before = seeded_registry.find_by_slug("openai").mentioned_in_count
        llm = _make_llm_mock([
            {"text": "OpenAI", "kind": "company", "confidence": 0.95, "snippet": "..."},
        ])
        ext = EntityExtractor(seeded_registry, llm_call=llm)
        ext.extract_entities("OpenAI released...", "test.md")
        after = seeded_registry.find_by_slug("openai").mentioned_in_count
        assert after == before + 1


# ---------------------------------------------------------------------------
# New candidate creation
# ---------------------------------------------------------------------------

class TestNewCandidateCreation:
    def test_high_confidence_creates_candidate(self, registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "Google DeepMind", "kind": "company", "confidence": 0.92, "snippet": "..."},
        ])
        ext = EntityExtractor(registry, llm_call=llm)
        result = ext.extract_entities("Google DeepMind paper.", "test.md")
        assert result.candidates_created == 1
        assert result.mentions[0].resolution == "new_candidate"
        entry = registry.find_by_slug("google-deepmind")
        assert entry is not None
        assert entry.entity_type == "company"

    def test_low_confidence_skipped(self, registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "SomeUnknown", "kind": "tool", "confidence": 0.3, "snippet": "..."},
        ])
        ext = EntityExtractor(registry, llm_call=llm)
        result = ext.extract_entities("Maybe SomeUnknown.", "test.md")
        assert result.skipped_low_confidence == 1
        assert result.mentions[0].resolution == "skipped"
        assert registry.find_by_slug("someunknown") is None

    def test_custom_confidence_threshold(self, registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "NewTool", "kind": "tool", "confidence": 0.6, "snippet": "..."},
        ])
        ext = EntityExtractor(registry, llm_call=llm, confidence_threshold=0.5)
        result = ext.extract_entities("Using NewTool.", "test.md")
        assert result.candidates_created == 1

    def test_source_evergreen_recorded(self, registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "Mistral AI", "kind": "company", "confidence": 0.9, "snippet": "..."},
        ])
        ext = EntityExtractor(registry, llm_call=llm)
        ext.extract_entities("Mistral AI is...", "source_article.md")
        entry = registry.find_by_slug("mistral-ai")
        assert "source_article.md" in entry.source_evergreens


# ---------------------------------------------------------------------------
# Invalid kind filtering
# ---------------------------------------------------------------------------

class TestKindFiltering:
    def test_concept_kind_skipped(self, registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "Attention Mechanism", "kind": "concept", "confidence": 0.95, "snippet": "..."},
        ])
        ext = EntityExtractor(registry, llm_call=llm)
        result = ext.extract_entities("attention is...", "test.md")
        assert result.skipped_low_confidence == 1
        assert result.mentions[0].resolution == "skipped"

    def test_empty_kind_skipped(self, registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "Something", "kind": "", "confidence": 0.9, "snippet": "..."},
        ])
        ext = EntityExtractor(registry, llm_call=llm)
        result = ext.extract_entities("something...", "test.md")
        assert len(result.mentions) == 0  # empty kind filtered before mention creation


# ---------------------------------------------------------------------------
# No LLM (None llm_call)
# ---------------------------------------------------------------------------

class TestNoLLM:
    def test_no_llm_returns_empty(self, registry: EntityRegistry) -> None:
        ext = EntityExtractor(registry, llm_call=None)
        result = ext.extract_entities("some content", "test.md")
        assert len(result.mentions) == 0

    def test_make_extractor_no_llm(self, entity_vault: Path) -> None:
        ext = make_extractor(entity_vault)
        result = ext.extract_entities("hello", "test.md")
        assert isinstance(result, ExtractionResult)


# ---------------------------------------------------------------------------
# LLM error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_llm_exception_captured(self, registry: EntityRegistry) -> None:
        def failing_llm(system, user, tokens):
            raise RuntimeError("API down")

        ext = EntityExtractor(registry, llm_call=failing_llm)
        result = ext.extract_entities("test", "test.md")
        assert len(result.errors) == 1
        assert "API down" in result.errors[0]


# ---------------------------------------------------------------------------
# Multiple entities in one extraction
# ---------------------------------------------------------------------------

class TestMultipleEntities:
    def test_mixed_resolution(self, seeded_registry: EntityRegistry) -> None:
        llm = _make_llm_mock([
            {"text": "Andrej Karpathy", "kind": "person", "confidence": 0.95, "snippet": "..."},
            {"text": "Google DeepMind", "kind": "company", "confidence": 0.9, "snippet": "..."},
            {"text": "SomeVague", "kind": "tool", "confidence": 0.3, "snippet": "..."},
        ])
        ext = EntityExtractor(seeded_registry, llm_call=llm)
        result = ext.extract_entities("mixed entities", "test.md")
        assert result.existing_matched == 1
        assert result.candidates_created == 1
        assert result.skipped_low_confidence == 1
        assert len(result.mentions) == 3


# ---------------------------------------------------------------------------
# File-based extraction
# ---------------------------------------------------------------------------

class TestFileExtraction:
    def test_extract_from_file(self, entity_vault: Path, registry: EntityRegistry) -> None:
        md = entity_vault / "test_article.md"
        md.write_text("# Test\nOpenAI released GPT-5.\n", encoding="utf-8")
        llm = _make_llm_mock([
            {"text": "OpenAI", "kind": "company", "confidence": 0.95, "snippet": "OpenAI released"},
        ])
        ext = EntityExtractor(registry, llm_call=llm)
        result = ext.extract_entities_from_file(md)
        assert result.source_file == "test_article.md"
        assert result.candidates_created == 1
