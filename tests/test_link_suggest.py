"""Stage A 2.2 — ovp-link-suggest backfill.

These tests pin the contract that the Phase 38 link-density audit relies on:
- pages with fewer than ``--min-links`` outbound wikilinks become suggestion
  targets;
- candidates already linked from the source are never re-proposed;
- ``--apply`` requires ``--confirm`` and is idempotent (the backfill marker
  is the gate).
"""

from __future__ import annotations

import json

import pytest

from ovp_pipeline.commands.link_suggest import (
    BACKFILL_HEADING,
    BACKFILL_MARKER,
    run_link_suggest,
)
from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_evergreens(temp_vault):
    """Three semantically related evergreens + one isolated deep_dive that
    mentions all three in prose but carries zero outbound wikilinks."""
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
    (evergreen / "function-calling.md").write_text(
        """---
note_id: function-calling
title: Function Calling
type: evergreen
date: 2026-04-23
---

# Function Calling

> 一句话定义: 让 LLM 调用外部工具的接口。

Function calling lets an AI agent invoke external tools through a structured
schema.
""",
        encoding="utf-8",
    )
    deep_dive_dir = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
    deep_dive_dir.mkdir(parents=True, exist_ok=True)
    (deep_dive_dir / "2026-04-23_AI_Stack_深度解读.md").write_text(
        """---
note_id: 2026-04-23-ai-stack
title: AI Stack Deep Dive
type: deep_dive
date: 2026-04-23
---

# AI Stack Deep Dive

This article explores how an AI agent uses RAG to ground its answers and
function calling to take actions. Retrieval-Augmented Generation pairs well
with structured tool use.
""",
        encoding="utf-8",
    )


def test_dry_run_emits_jsonl_for_under_linked_page(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    summary = run_link_suggest(temp_vault, min_links=3, suggestions_per_page=5)

    assert summary["applied"] is False
    assert summary["files_mutated"] == 0
    assert summary["pages_examined"] >= 1
    assert summary["suggestions_emitted"] >= 1

    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    assert log_path.exists()
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert rows, "expected at least one suggestion row"

    deep_dive_row = next(
        (r for r in rows if r["source_slug"] == "2026-04-23-ai-stack"),
        None,
    )
    assert deep_dive_row is not None, "deep_dive should appear in suggestions"
    assert deep_dive_row["target_slug"] in {"ai-agent", "rag", "function-calling"}
    assert deep_dive_row["rrf_score"] > 0


def test_apply_requires_confirm(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    with pytest.raises(ValueError, match="confirm"):
        run_link_suggest(temp_vault, apply=True, confirm=False)


def test_apply_appends_backfill_section_and_is_idempotent(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    summary = run_link_suggest(
        temp_vault,
        min_links=3,
        suggestions_per_page=3,
        apply=True,
        confirm=True,
    )
    assert summary["applied"] is True
    assert summary["files_mutated"] >= 1

    deep_dive_path = (
        temp_vault
        / "20-Areas"
        / "AI-Research"
        / "Topics"
        / "2026-04"
        / "2026-04-23_AI_Stack_深度解读.md"
    )
    body = deep_dive_path.read_text(encoding="utf-8")
    assert BACKFILL_HEADING in body
    assert BACKFILL_MARKER in body
    assert "[[ai-agent" in body or "[[rag" in body or "[[function-calling" in body

    # Re-run with --apply --confirm: the marker must short-circuit the rewrite.
    second = run_link_suggest(
        temp_vault,
        min_links=3,
        suggestions_per_page=3,
        apply=True,
        confirm=True,
    )
    assert second["files_mutated"] == 0
    body_after = deep_dive_path.read_text(encoding="utf-8")
    # Marker should still appear exactly once.
    assert body_after.count(BACKFILL_MARKER) == 1


def test_existing_link_targets_are_skipped(temp_vault):
    _seed_evergreens(temp_vault)
    # Pre-link the deep_dive to one evergreen so the candidate is already known.
    deep_dive_path = (
        temp_vault
        / "20-Areas"
        / "AI-Research"
        / "Topics"
        / "2026-04"
        / "2026-04-23_AI_Stack_深度解读.md"
    )
    body = deep_dive_path.read_text(encoding="utf-8")
    deep_dive_path.write_text(
        body + "\n\nSee [[ai-agent]] for the agent primitive.\n", encoding="utf-8"
    )

    rebuild_knowledge_index(temp_vault)
    summary = run_link_suggest(temp_vault, min_links=3, suggestions_per_page=5)

    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    deep_dive_rows = [r for r in rows if r["source_slug"] == "2026-04-23-ai-stack"]
    assert all(
        r["target_slug"] != "ai-agent" for r in deep_dive_rows
    ), "candidates already linked from the source must be excluded"


def test_min_links_threshold_excludes_well_linked_pages(temp_vault):
    _seed_evergreens(temp_vault)
    deep_dive_path = (
        temp_vault
        / "20-Areas"
        / "AI-Research"
        / "Topics"
        / "2026-04"
        / "2026-04-23_AI_Stack_深度解读.md"
    )
    body = deep_dive_path.read_text(encoding="utf-8")
    # Make the deep_dive carry 3 outbound wikilinks → no longer under-linked.
    deep_dive_path.write_text(
        body + "\n\nSee [[ai-agent]], [[rag]], and [[function-calling]] for primitives.\n",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    summary = run_link_suggest(temp_vault, min_links=3)

    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert all(
        r["source_slug"] != "2026-04-23-ai-stack" for r in rows
    ), "pages at or above the threshold must not appear in suggestions"


def test_log_path_round_trips_through_summary(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    summary = run_link_suggest(temp_vault, min_links=3, limit=2)
    log_path = summary["log_path"]
    assert log_path.endswith(".jsonl")
    # The summary's count must equal the number of rows written.
    rows = [
        json.loads(line)
        for line in (temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert len(rows) == summary["suggestions_emitted"]


def test_no_pages_when_index_empty(temp_vault):
    """Empty vault → rebuild creates the schema → run returns zero rows."""
    rebuild_knowledge_index(temp_vault)
    summary = run_link_suggest(temp_vault, min_links=3)
    assert summary["pages_examined"] == 0
    assert summary["suggestions_emitted"] == 0
    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8") == ""
