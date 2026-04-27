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
    GATE_CACHE_FILENAME,
    LINK_SUGGEST_GATE_PROMPT,
    RRF_K,
    _parse_gate_response,
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

    summary = run_link_suggest(temp_vault, min_links=3, suggestions_per_page=5, use_llm_gate=False)

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
        use_llm_gate=False,
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
        use_llm_gate=False,
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
    summary = run_link_suggest(temp_vault, min_links=3, suggestions_per_page=5, use_llm_gate=False)

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
    summary = run_link_suggest(temp_vault, min_links=3, use_llm_gate=False)

    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert all(
        r["source_slug"] != "2026-04-23-ai-stack" for r in rows
    ), "pages at or above the threshold must not appear in suggestions"


def test_log_path_round_trips_through_summary(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    summary = run_link_suggest(temp_vault, min_links=3, limit=2, use_llm_gate=False)
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
    summary = run_link_suggest(temp_vault, min_links=3, use_llm_gate=False)
    assert summary["pages_examined"] == 0
    assert summary["suggestions_emitted"] == 0
    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8") == ""


def test_bm25_branch_survives_hyphens_and_colons_in_query(temp_vault):
    """Regression: prose like ``Retrieval-Augmented`` or ``AI: ...`` used to
    crash FTS5 (`no such column: step`); the blind ``except`` then silently
    collapsed BM25 to ``[]``. With both branches contributing, at least one
    suggestion's ``rrf_score`` must exceed the single-branch ceiling of
    ``1/(RRF_K+1)`` — that's the algebraic proof that BM25 didn't quietly
    disappear."""
    _seed_evergreens(temp_vault)
    deep_dive_path = (
        temp_vault
        / "20-Areas"
        / "AI-Research"
        / "Topics"
        / "2026-04"
        / "2026-04-23_AI_Stack_深度解读.md"
    )
    # Overwrite with content packed with FTS5-hostile syntax in title and body.
    deep_dive_path.write_text(
        """---
note_id: 2026-04-23-ai-stack
title: "AI Stack: Retrieval-Augmented Multi-Step Agents"
type: deep_dive
date: 2026-04-23
---

# AI Stack: Retrieval-Augmented Multi-Step Agents

Retrieval-Augmented Generation pairs with multi-step AI agents that use
function-calling to invoke external tools. The pattern: agent + RAG +
function-calling, all in one stack.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    summary = run_link_suggest(temp_vault, min_links=3, suggestions_per_page=5, use_llm_gate=False)

    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    deep_dive_rows = [r for r in rows if r["source_slug"] == "2026-04-23-ai-stack"]
    assert deep_dive_rows, "deep_dive with FTS5-hostile prose must still get suggestions"
    single_branch_ceiling = 1.0 / (RRF_K + 1)
    assert any(
        r["rrf_score"] > single_branch_ceiling for r in deep_dive_rows
    ), "at least one row must score above the single-branch ceiling — proves BM25 fired"


# ---------------------------------------------------------------------------
# Phase 38 Stage A.2 — LLM second-opinion gate
# ---------------------------------------------------------------------------


def _make_recording_gate(decisions_by_slug: dict[str, dict]) -> tuple[list, callable]:
    """Build a fake gate client that returns canned decisions per candidate
    slug. Returns ``(call_log, client)`` so tests can assert call counts."""
    calls: list[tuple[str, str]] = []

    def _client(system_prompt: str, user_prompt: str) -> str:
        calls.append((system_prompt, user_prompt))
        payload = json.loads(user_prompt)
        decisions = []
        for cand in payload["candidates"]:
            slug = cand["slug"]
            if slug in decisions_by_slug:
                d = decisions_by_slug[slug]
                decisions.append(
                    {
                        "slug": slug,
                        "decision": d["decision"],
                        "confidence": d["confidence"],
                        "rationale": d.get("rationale", ""),
                    }
                )
            else:
                decisions.append(
                    {"slug": slug, "decision": "skip", "confidence": 0.0, "rationale": "unknown"}
                )
        return json.dumps({"decisions": decisions})

    return calls, _client


def test_gate_filters_by_threshold_and_decision(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    canned = {
        "ai-agent": {"decision": "link", "confidence": 0.9, "rationale": "core"},
        "rag": {"decision": "link", "confidence": 0.5, "rationale": "weak"},  # below threshold
        "function-calling": {"decision": "skip", "confidence": 0.95, "rationale": "off-topic"},
    }
    _, client = _make_recording_gate(canned)

    summary = run_link_suggest(
        temp_vault,
        min_links=3,
        suggestions_per_page=5,
        gate_threshold=0.6,
        gate_client=client,
    )

    assert summary["gate_enabled"] is True
    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    deep = [r for r in rows if r["source_slug"] == "2026-04-23-ai-stack"]
    # Every candidate should be present with a decision/confidence/rationale.
    assert deep
    for row in deep:
        assert "decision" in row and row["decision"] in {"link", "skip"}
        assert "confidence" in row
        assert "rationale" in row
    # Only ai-agent (link, 0.9) should pass — rag is below threshold, function-calling skipped.
    passed = [r for r in deep if r["decision"] == "link" and r["confidence"] >= 0.6]
    passed_slugs = {r["target_slug"] for r in passed}
    assert "ai-agent" in passed_slugs
    assert "rag" not in passed_slugs
    assert "function-calling" not in passed_slugs
    assert summary["gate_passed"] >= 1


def test_gate_apply_only_writes_link_decisions(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    canned = {
        "ai-agent": {"decision": "link", "confidence": 0.9, "rationale": "core"},
        "rag": {"decision": "skip", "confidence": 0.95, "rationale": "off-topic"},
        "function-calling": {"decision": "skip", "confidence": 0.9, "rationale": "off-topic"},
    }
    _, client = _make_recording_gate(canned)

    summary = run_link_suggest(
        temp_vault,
        min_links=3,
        suggestions_per_page=5,
        apply=True,
        confirm=True,
        gate_threshold=0.6,
        gate_client=client,
    )
    assert summary["applied"] is True

    deep_path = (
        temp_vault
        / "20-Areas"
        / "AI-Research"
        / "Topics"
        / "2026-04"
        / "2026-04-23_AI_Stack_深度解读.md"
    )
    body = deep_path.read_text(encoding="utf-8")
    assert BACKFILL_MARKER in body
    assert "[[ai-agent" in body
    # Skipped candidates must NOT appear in the backfill section.
    backfill_section = body[body.index(BACKFILL_MARKER) :]
    assert "[[rag" not in backfill_section
    assert "[[function-calling" not in backfill_section


def test_gate_cache_skips_redundant_llm_calls(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    canned = {
        "ai-agent": {"decision": "link", "confidence": 0.9, "rationale": "core"},
        "rag": {"decision": "link", "confidence": 0.85, "rationale": "ground"},
        "function-calling": {"decision": "skip", "confidence": 0.9, "rationale": "off"},
    }
    calls, client = _make_recording_gate(canned)

    run_link_suggest(
        temp_vault,
        min_links=3,
        suggestions_per_page=5,
        gate_client=client,
    )
    first_calls = len(calls)
    assert first_calls >= 1

    cache_path = temp_vault / "60-Logs" / "link-suggestions" / GATE_CACHE_FILENAME
    assert cache_path.exists()

    # Re-run: cache should absorb every candidate, so no new gate calls fire.
    run_link_suggest(
        temp_vault,
        min_links=3,
        suggestions_per_page=5,
        gate_client=client,
    )
    assert len(calls) == first_calls, "cache must short-circuit the gate on re-run"


def test_no_llm_gate_falls_back_to_rrf_only(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    summary = run_link_suggest(temp_vault, min_links=3, suggestions_per_page=5, use_llm_gate=False)
    assert summary["gate_enabled"] is False
    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    # Without the gate, rows must NOT carry decision/confidence/rationale.
    for row in rows:
        assert "decision" not in row
        assert "confidence" not in row


def test_gate_error_marks_candidates_as_skip(temp_vault):
    _seed_evergreens(temp_vault)
    rebuild_knowledge_index(temp_vault)

    def _broken_client(system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("simulated LLM outage")

    summary = run_link_suggest(
        temp_vault,
        min_links=3,
        suggestions_per_page=5,
        apply=True,
        confirm=True,
        gate_client=_broken_client,
    )
    assert summary["files_mutated"] == 0  # everything skipped → nothing to write
    log_path = temp_vault / "60-Logs" / "link-suggestions" / f"{summary['run_id']}.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    # All rows must be marked skip with the gate-error rationale.
    skipped = [r for r in rows if r.get("decision") == "skip"]
    assert skipped
    assert any("gate error" in r["rationale"] for r in skipped)


def test_parse_gate_response_handles_fenced_json():
    fenced = """```json
{"decisions": [{"slug": "x", "decision": "link", "confidence": 0.8, "rationale": "ok"}]}
```"""
    parsed = _parse_gate_response(fenced)
    assert len(parsed) == 1
    assert parsed[0]["slug"] == "x"
    assert parsed[0]["decision"] == "link"
    assert parsed[0]["confidence"] == 0.8


def test_parse_gate_response_drops_invalid_decisions():
    text = json.dumps(
        {
            "decisions": [
                {"slug": "ok", "decision": "link", "confidence": 0.9},
                {"slug": "", "decision": "link", "confidence": 0.9},  # empty slug → drop
                {"slug": "bad", "decision": "maybe", "confidence": 0.9},  # bad decision → drop
                {"slug": "clamp", "decision": "link", "confidence": 2.5},  # clamps to 1.0
            ]
        }
    )
    parsed = _parse_gate_response(text)
    by_slug = {p["slug"]: p for p in parsed}
    assert "ok" in by_slug
    assert "" not in by_slug
    assert "bad" not in by_slug
    assert by_slug["clamp"]["confidence"] == 1.0


def test_gate_prompt_is_chinese_with_strict_rubric():
    # Sanity: the prompt that ships must enforce the conservative confidence rubric.
    assert "0.6" in LINK_SUGGEST_GATE_PROMPT
    assert "skip" in LINK_SUGGEST_GATE_PROMPT
