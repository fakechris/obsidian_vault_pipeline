"""Phase B.6: Entity Layer Benchmark Q1-Q5 验收测试.

验证 Entity Layer 核心能力:
Q1: 按别名解析实体 — "ChatGPT" → gpt, "MCP" → model-context-protocol
Q2: 按类型查询 — 列出所有 person / company / tool
Q3: 实体候选文件生成 — _Candidates/ 目录下有正确 frontmatter 的 .md
Q4: Promote 生命周期 — candidate → active, 生成 Entity .md
Q5: Entity 注册表持久化 — JSONL round-trip + alias index rebuild
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ovp_pipeline.entity_registry import (
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_REJECTED,
    EntityEntry,
    EntityRegistry,
)
from ovp_pipeline.promote_entities import (
    promote_entity,
    merge_entity,
    reject_entity,
    write_candidate_file,
    write_entity_file,
)
from ovp_pipeline.identity import canonicalize_note_id


@pytest.fixture
def vault(tmp_path):
    """Create a temp vault with Entity directories."""
    (tmp_path / "10-Knowledge" / "Entity" / "_Candidates").mkdir(parents=True)
    (tmp_path / "10-Knowledge" / "Evergreen").mkdir(parents=True)
    (tmp_path / "20-Areas" / "AI-Research" / "Topics" / "2026-04").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def seeded_registry(vault):
    """Create a registry with seed entities for benchmark queries."""
    registry = EntityRegistry(vault).load()

    seeds = [
        ("anthropic", "Anthropic", "company", ["Anthropic AI"]),
        ("openai", "OpenAI", "company", ["Open AI"]),
        ("google-deepmind", "Google DeepMind", "company", ["DeepMind"]),
        ("meta", "Meta", "company", ["Meta Platforms", "Facebook"]),
        ("github", "GitHub", "company", ["GitHub Inc"]),
        ("claude", "Claude", "tool", ["Claude AI", "Claude Code"]),
        ("gpt", "GPT", "tool", ["GPT-4", "GPT-4o", "ChatGPT"]),
        ("gemini", "Gemini", "tool", ["Gemini Pro", "Gemini Ultra"]),
        ("model-context-protocol", "Model Context Protocol", "tool", ["MCP"]),
        ("obsidian", "Obsidian", "tool", ["Obsidian.md"]),
        ("cursor", "Cursor", "tool", ["Cursor IDE"]),
        ("andrej-karpathy", "Andrej Karpathy", "person", ["Karpathy"]),
        ("dario-amodei", "Dario Amodei", "person", []),
        ("sam-altman", "Sam Altman", "person", []),
        ("transformer", "Transformer", "paper", ["Attention Is All You Need"]),
        ("react-paper", "ReAct", "paper", ["ReAct Framework"]),
    ]

    for slug, title, etype, aliases in seeds:
        registry.upsert_candidate(
            slug=slug, title=title, entity_type=etype,
            aliases=aliases, confidence=0.95,
        )

    registry.save()
    return registry


class TestQ1_AliasResolution:
    """Q1: 按别名解析实体 — 别名/缩写/变体 → 正确的 canonical entity."""

    def test_alias_chatgpt_resolves_to_gpt(self, seeded_registry):
        match = seeded_registry.resolve_mention("ChatGPT")
        assert match is not None
        assert match.slug == "gpt"
        assert match.entity_type == "tool"

    def test_alias_mcp_resolves_to_model_context_protocol(self, seeded_registry):
        match = seeded_registry.resolve_mention("MCP")
        assert match is not None
        assert match.slug == "model-context-protocol"

    def test_alias_karpathy_resolves_to_person(self, seeded_registry):
        match = seeded_registry.resolve_mention("Karpathy")
        assert match is not None
        assert match.slug == "andrej-karpathy"
        assert match.entity_type == "person"

    def test_alias_facebook_resolves_to_meta(self, seeded_registry):
        match = seeded_registry.resolve_mention("Facebook")
        assert match is not None
        assert match.slug == "meta"
        assert match.entity_type == "company"

    def test_alias_deepmind_resolves_to_google_deepmind(self, seeded_registry):
        match = seeded_registry.resolve_mention("DeepMind")
        assert match is not None
        assert match.slug == "google-deepmind"

    def test_alias_attention_is_all_you_need_resolves_to_transformer(self, seeded_registry):
        match = seeded_registry.resolve_mention("Attention Is All You Need")
        assert match is not None
        assert match.slug == "transformer"
        assert match.entity_type == "paper"

    def test_direct_slug_resolves(self, seeded_registry):
        match = seeded_registry.resolve_mention("openai")
        assert match is not None
        assert match.slug == "openai"

    def test_unknown_mention_returns_none(self, seeded_registry):
        assert seeded_registry.resolve_mention("NonExistentEntity9999") is None


class TestQ2_TypeQuery:
    """Q2: 按类型查询 — 列出所有 person / company / tool / paper."""

    def test_list_all_companies(self, seeded_registry):
        companies = seeded_registry.find_by_type("company")
        slugs = {e.slug for e in companies}
        assert "anthropic" in slugs
        assert "openai" in slugs
        assert "meta" in slugs
        assert "github" in slugs
        assert "google-deepmind" in slugs
        assert len(companies) == 5

    def test_list_all_persons(self, seeded_registry):
        persons = seeded_registry.find_by_type("person")
        slugs = {e.slug for e in persons}
        assert "andrej-karpathy" in slugs
        assert "dario-amodei" in slugs
        assert "sam-altman" in slugs
        assert len(persons) == 3

    def test_list_all_papers(self, seeded_registry):
        papers = seeded_registry.find_by_type("paper")
        slugs = {e.slug for e in papers}
        assert "transformer" in slugs
        assert "react-paper" in slugs
        assert len(papers) == 2

    def test_count_by_type(self, seeded_registry):
        counts = seeded_registry.count_by_type()
        assert counts["company"] == 5
        assert counts["person"] == 3
        assert counts["tool"] == 6
        assert counts["paper"] == 2

    def test_empty_type_returns_empty(self, seeded_registry):
        events = seeded_registry.find_by_type("event")
        assert events == []


class TestQ3_CandidateFileGeneration:
    """Q3: 实体候选文件生成 — _Candidates/ 正确 frontmatter."""

    def test_candidate_file_created(self, vault, seeded_registry):
        entry = seeded_registry.find_by_slug("anthropic")
        write_candidate_file(vault, entry, dry_run=False)

        candidate_path = vault / "10-Knowledge/Entity/_Candidates/anthropic.md"
        assert candidate_path.exists()

    def test_candidate_frontmatter_has_entity_type(self, vault, seeded_registry):
        entry = seeded_registry.find_by_slug("claude")
        write_candidate_file(vault, entry, dry_run=False)

        content = (vault / "10-Knowledge/Entity/_Candidates/claude.md").read_text()
        assert "entity_type: tool" in content
        assert "type: entity" in content
        assert "status: candidate" in content
        assert 'note_id: claude' in content

    def test_candidate_frontmatter_has_aliases(self, vault, seeded_registry):
        entry = seeded_registry.find_by_slug("gpt")
        write_candidate_file(vault, entry, dry_run=False)

        content = (vault / "10-Knowledge/Entity/_Candidates/gpt.md").read_text()
        assert "GPT-4" in content
        assert "ChatGPT" in content

    def test_candidate_dry_run_does_not_create(self, vault, seeded_registry):
        entry = seeded_registry.find_by_slug("anthropic")
        write_candidate_file(vault, entry, dry_run=True)

        assert not (vault / "10-Knowledge/Entity/_Candidates/anthropic.md").exists()


class TestQ4_PromoteLifecycle:
    """Q4: Promote 生命周期 — candidate → active → Entity .md."""

    def test_promote_changes_status(self, vault, seeded_registry):
        seeded_registry.save()
        entry = seeded_registry.find_by_slug("anthropic")
        assert entry.status == STATUS_CANDIDATE

        promote_entity(vault, "anthropic", dry_run=False)
        reloaded = EntityRegistry(vault).load()
        entry = reloaded.find_by_slug("anthropic")
        assert entry.status == STATUS_ACTIVE

    def test_promote_creates_entity_md(self, vault, seeded_registry):
        seeded_registry.save()
        promote_entity(vault, "anthropic", dry_run=False)

        entity_path = vault / "10-Knowledge/Entity/anthropic.md"
        assert entity_path.exists()

        content = entity_path.read_text()
        assert "entity_type: company" in content
        assert "type: entity" in content
        assert "Anthropic" in content

    def test_promote_removes_candidate_file(self, vault, seeded_registry):
        entry = seeded_registry.find_by_slug("anthropic")
        write_candidate_file(vault, entry, dry_run=False)
        candidate_path = vault / "10-Knowledge/Entity/_Candidates/anthropic.md"
        assert candidate_path.exists()

        seeded_registry.save()
        promote_entity(vault, "anthropic", dry_run=False)
        assert not candidate_path.exists()

    def test_reject_entity(self, vault, seeded_registry):
        seeded_registry.save()
        reject_entity(vault, "anthropic", dry_run=False)
        reloaded = EntityRegistry(vault).load()
        entry = reloaded.find_by_slug("anthropic")
        assert entry.status == STATUS_REJECTED

        assert reloaded.resolve_mention("Anthropic AI") is None

    def test_merge_entity(self, vault, seeded_registry):
        seeded_registry.upsert_candidate(
            slug="openai-inc", title="OpenAI Inc", entity_type="company",
            aliases=["OpenAI Corporation"], confidence=0.8,
        )
        seeded_registry.save()

        merge_entity(vault, "openai-inc", "openai", dry_run=False)

        reloaded = EntityRegistry(vault).load()
        winner = reloaded.find_by_slug("openai")
        assert winner is not None
        assert "OpenAI Corporation" in winner.aliases

    def test_promote_dry_run(self, vault, seeded_registry):
        seeded_registry.save()
        promote_entity(vault, "anthropic", dry_run=True)
        reloaded = EntityRegistry(vault).load()
        entry = reloaded.find_by_slug("anthropic")
        assert entry.status == STATUS_CANDIDATE


class TestQ5_Persistence:
    """Q5: Entity 注册表持久化 — JSONL round-trip + alias index."""

    def test_save_and_reload(self, vault, seeded_registry):
        seeded_registry.save()

        reloaded = EntityRegistry(vault).load()
        assert len(reloaded) == len(seeded_registry)

        entry = reloaded.find_by_slug("anthropic")
        assert entry is not None
        assert entry.title == "Anthropic"
        assert entry.entity_type == "company"

    def test_alias_survives_reload(self, vault, seeded_registry):
        seeded_registry.save()

        reloaded = EntityRegistry(vault).load()
        match = reloaded.resolve_mention("ChatGPT")
        assert match is not None
        assert match.slug == "gpt"

    def test_alias_index_file_created(self, vault, seeded_registry):
        seeded_registry.save()

        alias_path = vault / "10-Knowledge/Entity/_aliases.json"
        assert alias_path.exists()

        doc = json.loads(alias_path.read_text())
        aliases = doc.get("aliases", doc)
        assert aliases.get("chatgpt") == "gpt"
        assert aliases.get("mcp") == "model-context-protocol"
        assert aliases.get("karpathy") == "andrej-karpathy"

    def test_jsonl_line_count_matches(self, vault, seeded_registry):
        seeded_registry.save()

        jsonl_path = vault / "10-Knowledge/Entity/entity-registry.jsonl"
        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == len(seeded_registry)

    def test_promoted_state_persists(self, vault, seeded_registry):
        seeded_registry.save()
        promote_entity(vault, "anthropic", dry_run=False)

        reloaded = EntityRegistry(vault).load()
        entry = reloaded.find_by_slug("anthropic")
        assert entry.status == STATUS_ACTIVE

    def test_rejected_state_persists(self, vault, seeded_registry):
        seeded_registry.save()
        reject_entity(vault, "anthropic", dry_run=False)

        reloaded = EntityRegistry(vault).load()
        entry = reloaded.find_by_slug("anthropic")
        assert entry.status == STATUS_REJECTED
        assert reloaded.resolve_mention("Anthropic AI") is None
