"""Stage A 2.1 — extractor retrieval injection.

Pins the contract that the Phase 38 link-density fix relies on at extraction
time: every new evergreen is born grounded in the existing concept registry,
not in a vacuum.

- ``_format_related_block`` produces a deterministic checklist for the prompt.
- ``_retrieve_related_for_extraction`` short-circuits when ``vault_dir`` is None
  (so legacy callers keep working) and pulls definitions from the registry when
  available.
- ``extract_concepts`` injects the "已有概念目录" block into ``user_prompt``
  whenever retrieval finds anything, so the LLM is forced to reuse known slugs.
- ``process_file`` emits an ``evergreen_low_link`` audit event whenever the LLM
  returns a concept with fewer than 3 related_concepts — this is the Phase 38
  watchdog for "the prompt didn't bind".
"""

from __future__ import annotations

import json

from ovp_pipeline.auto_evergreen_extractor import (
    AutoEvergreenExtractor,
    EvergreenExtractor,
    PipelineLogger,
)
from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_evergreens(temp_vault):
    evergreen = temp_vault / "10-Knowledge" / "Evergreen"
    (evergreen / "ai-agent.md").write_text(
        """---
note_id: ai-agent
title: AI Agent
type: evergreen
date: 2026-04-23
---

# AI Agent

> 一句话定义: 能感知环境并自主决策的 AI 系统。

AI agents combine planning, memory, and tool use to execute multi-step tasks.
""",
        encoding="utf-8",
    )
    (evergreen / "rag.md").write_text(
        """---
note_id: rag
title: RAG
type: evergreen
date: 2026-04-23
---

# RAG

> 一句话定义: 检索增强生成。

Retrieval-Augmented Generation grounds language model responses in external
documents to reduce hallucination.
""",
        encoding="utf-8",
    )


class _FakeLLM:
    """Captures the (system_prompt, user_prompt) handed to ``generate`` and
    replays a canned JSON response. Mirrors ``LiteLLMClient.generate``'s
    signature so the extractor can't tell the difference."""

    def __init__(self, response_concepts):
        self.response_concepts = response_concepts
        self.calls: list[dict] = []

    def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
            }
        )
        return json.dumps(self.response_concepts, ensure_ascii=False)


def test_format_related_block_empty_returns_empty_string():
    assert EvergreenExtractor._format_related_block([]) == ""


def test_format_related_block_renders_slug_title_definition():
    block = EvergreenExtractor._format_related_block(
        [
            {"slug": "ai-agent", "title": "AI Agent", "definition": "自主决策的 AI 系统。"},
            {"slug": "rag", "title": "RAG", "definition": ""},
        ]
    )
    assert "已有概念目录" in block
    assert "`ai-agent` — AI Agent — 自主决策的 AI 系统。" in block
    assert "`rag` — RAG" in block
    # No definition → no trailing em-dash for that row.
    assert "`rag` — RAG — " not in block


def test_format_related_block_truncates_long_definitions():
    long_def = "x" * 200
    block = EvergreenExtractor._format_related_block(
        [{"slug": "s", "title": "T", "definition": long_def}]
    )
    # Truncation marker (…) appears, and the row never crosses ~100 chars.
    assert "..." in block
    assert all(len(line) < 120 for line in block.splitlines())


def test_retrieve_returns_empty_when_vault_dir_is_none(temp_vault):
    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    extractor = EvergreenExtractor(_FakeLLM([]), logger, vault_dir=None)
    assert extractor._retrieve_related_for_extraction("anything") == []


def test_retrieve_pulls_hits_from_knowledge_index(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    extractor = EvergreenExtractor(_FakeLLM([]), logger, vault_dir=temp_vault)

    hits = extractor._retrieve_related_for_extraction("AI agent planning memory")
    slugs = {h["slug"] for h in hits}
    assert "ai-agent" in slugs


def test_extract_concepts_injects_related_block_into_user_prompt(temp_vault, tmp_path):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    fake_llm = _FakeLLM([])
    extractor = EvergreenExtractor(fake_llm, logger, vault_dir=temp_vault)

    fake_path = tmp_path / "ai_agent_deep_dive.md"
    fake_path.write_text(
        "AI agents use planning and memory to execute multi-step tasks. "
        "Function calling lets them invoke external tools.",
        encoding="utf-8",
    )

    extractor.extract_concepts(fake_path, fake_path.read_text(encoding="utf-8"))

    assert len(fake_llm.calls) == 1
    user_prompt = fake_llm.calls[0]["user_prompt"]
    assert "已有概念目录" in user_prompt
    assert "ai-agent" in user_prompt


def test_extract_concepts_omits_block_when_vault_dir_none(temp_vault, tmp_path):
    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    fake_llm = _FakeLLM([])
    extractor = EvergreenExtractor(fake_llm, logger, vault_dir=None)

    fake_path = tmp_path / "irrelevant.md"
    fake_path.write_text("body", encoding="utf-8")
    extractor.extract_concepts(fake_path, "body")

    assert len(fake_llm.calls) == 1
    assert "已有概念目录" not in fake_llm.calls[0]["user_prompt"]


# BL-058: ``evergreen_low_link`` audit event was dropped — v2 prompt
# allows 0-5 related_concepts (宁缺勿滥), so missing links are no
# longer a regression signal.  Two tests that asserted the event were
# removed with this BL-058 migration.
