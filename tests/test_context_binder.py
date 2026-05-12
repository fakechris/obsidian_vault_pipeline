"""Tests for M21a / BL-083 — two-layer context binder."""

from __future__ import annotations

from pathlib import Path

import pytest

from ovp_pipeline.context_binder import (
    ANCHOR_BUDGET_FRACTION,
    ANCHOR_KINDS,
    SYSTEM_FRAME_MARGIN_TOKENS,
    TURN_HISTORY_VERBATIM_K,
    AnchorContext,
    ContextManifest,
    RetrievalContext,
    TurnPair,
    build_anchor_context,
    build_chat_context,
    build_retrieval_context,
    estimate_tokens,
    manifest_to_lines,
    select_verbatim_window,
    should_rebuild_summary,
    split_budget,
)

# ── budgeting ──────────────────────────────────────────────────


def test_estimate_tokens_returns_zero_for_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_under_estimates_chars():
    # 4 chars/token proxy → 1000 chars ≈ 250 tokens.
    assert estimate_tokens("x" * 1000) == 250


def test_split_budget_60_40_after_margin():
    anchor, retrieval = split_budget(10_000)
    available = 10_000 - SYSTEM_FRAME_MARGIN_TOKENS
    assert anchor == int(available * ANCHOR_BUDGET_FRACTION)
    assert retrieval == available - anchor


def test_split_budget_subtracts_turn_history_tokens():
    a1, r1 = split_budget(10_000, turn_history_tokens=0)
    a2, r2 = split_budget(10_000, turn_history_tokens=2000)
    assert a1 + r1 - 2000 == a2 + r2


def test_split_budget_returns_zero_when_cap_too_tight():
    assert split_budget(SYSTEM_FRAME_MARGIN_TOKENS // 2) == (0, 0)


# ── standalone anchor ──────────────────────────────────────────


def test_standalone_anchor_returns_empty_context(tmp_path: Path):
    ctx = build_anchor_context(
        tmp_path,
        anchor_kind="standalone",
        anchor_ref="",
        budget_tokens=10_000,
    )
    assert ctx.kind == "standalone"
    assert ctx.included_anchor == ""
    assert ctx.included_evergreens == ()
    assert ctx.token_estimate == 0


def test_build_chat_context_rejects_unknown_anchor_kind(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown anchor kind"):
        build_chat_context(
            tmp_path,
            anchor_kind="bogus",
            anchor_ref="x",
            user_message="hi",
            profile_input_cap=16_000,
        )


def test_anchor_kinds_locked_set():
    assert ANCHOR_KINDS == frozenset({"note", "object", "crystal", "standalone"})


# ── note anchor ────────────────────────────────────────────────


def test_note_anchor_loads_body_within_budget(tmp_path: Path):
    note = tmp_path / "20-Areas/note.md"
    note.parent.mkdir(parents=True)
    body = "# Note title\n\nSome content with [[evergreen-a]] and [[evergreen-b]]."
    note.write_text(body, encoding="utf-8")

    ctx = build_anchor_context(
        tmp_path,
        anchor_kind="note",
        anchor_ref="20-Areas/note.md",
        budget_tokens=10_000,
    )
    assert ctx.kind == "note"
    assert ctx.ref == "20-Areas/note.md"
    assert "Note title" in ctx.included_anchor
    assert "evergreen-a" in ctx.included_evergreens
    assert "evergreen-b" in ctx.included_evergreens


def test_note_anchor_missing_file_returns_empty_body(tmp_path: Path):
    """Missing anchor file degrades to empty body — the handler can
    still answer from retrieval + USER + RULES."""
    ctx = build_anchor_context(
        tmp_path,
        anchor_kind="note",
        anchor_ref="20-Areas/nope.md",
        budget_tokens=10_000,
    )
    assert ctx.included_anchor == ""
    assert ctx.included_evergreens == ()


def test_anchor_body_truncated_to_budget(tmp_path: Path):
    note = tmp_path / "20-Areas/big.md"
    note.parent.mkdir(parents=True)
    # 40k chars ~= 10k tokens; budget 200 tokens means ~800 chars max.
    note.write_text("A" * 40_000, encoding="utf-8")
    ctx = build_anchor_context(
        tmp_path,
        anchor_kind="note",
        anchor_ref="20-Areas/big.md",
        budget_tokens=200,
    )
    # Body was truncated; never empty when source exists.
    assert ctx.included_anchor
    assert "[truncated" in ctx.included_anchor
    assert ctx.token_estimate <= 250  # close to budget, not 10k


def test_anchor_kind_propagated_to_manifest(tmp_path: Path):
    """Even when the body is empty, the kind/ref round-trip."""
    body, manifest = build_chat_context(
        tmp_path,
        anchor_kind="standalone",
        anchor_ref="",
        user_message="general question",
        profile_input_cap=16_000,
    )
    assert manifest.anchor.kind == "standalone"
    assert manifest.anchor.ref == ""


# ── retrieval layer ────────────────────────────────────────────


def test_empty_query_skips_retrieval(tmp_path: Path):
    ctx = build_retrieval_context(tmp_path, query="", budget_tokens=10_000)
    assert ctx.included_objects == ()
    assert ctx.included_crystals == ()


def test_zero_budget_skips_retrieval(tmp_path: Path):
    ctx = build_retrieval_context(tmp_path, query="anything", budget_tokens=0)
    assert ctx.included_objects == ()


def test_missing_knowledge_db_returns_empty_retrieval(tmp_path: Path):
    """Defensive: a vault without knowledge.db doesn't crash."""
    ctx = build_retrieval_context(
        tmp_path,
        query="memory architecture",
        budget_tokens=10_000,
    )
    # No DB → empty retrieval, but the call returns cleanly.
    assert isinstance(ctx, RetrievalContext)
    assert ctx.query == "memory architecture"


# ── full build path ────────────────────────────────────────────


def test_build_chat_context_returns_prompt_and_manifest(tmp_path: Path):
    note = tmp_path / "20-Areas/x.md"
    note.parent.mkdir(parents=True)
    note.write_text("# X\n\nContent body.", encoding="utf-8")

    body, manifest = build_chat_context(
        tmp_path,
        anchor_kind="note",
        anchor_ref="20-Areas/x.md",
        user_message="What does X say?",
        profile_input_cap=16_000,
    )
    assert isinstance(manifest, ContextManifest)
    assert "Content body." in body
    assert manifest.context_built_at.endswith("Z")
    assert manifest.anchor.kind == "note"
    assert manifest.token_estimate_total > 0


def test_build_chat_context_includes_user_profile_when_present(tmp_path: Path):
    (tmp_path / "00-Polaris").mkdir()
    (tmp_path / "00-Polaris/USER.md").write_text(
        "# About Me\nI'm a researcher.\n",
        encoding="utf-8",
    )
    body, _ = build_chat_context(
        tmp_path,
        anchor_kind="standalone",
        anchor_ref="",
        user_message="hi",
        profile_input_cap=16_000,
    )
    assert "User Profile" in body or "researcher" in body


# ── manifest_to_lines ──────────────────────────────────────────


def test_manifest_to_lines_round_trips_core_fields():
    anchor = AnchorContext(
        kind="note",
        ref="20-Areas/x.md",
        included_anchor="body",
        included_evergreens=("a", "b"),
        token_estimate=200,
    )
    retrieval = RetrievalContext(
        query="q",
        included_objects=("obj-1",),
        token_estimate=300,
    )
    manifest = ContextManifest(
        anchor=anchor,
        retrieval=retrieval,
        token_estimate_total=500,
        context_built_at="2026-05-12T11:00:00Z",
    )
    lines = manifest_to_lines(manifest)
    joined = "\n".join(lines)
    assert "context_built_at: 2026-05-12T11:00:00Z" in joined
    assert "token_estimate: 500" in joined
    assert "anchor_kind: note" in joined
    assert "anchor_ref: 20-Areas/x.md" in joined
    assert "- a" in joined
    assert "- obj-1" in joined


def test_manifest_to_lines_records_omissions():
    manifest = ContextManifest(
        anchor=AnchorContext(kind="note", ref="x.md"),
        retrieval=RetrievalContext(query="q"),
        omitted_count=3,
        omitted_reason="token_budget",
        token_estimate_total=0,
        context_built_at="t",
    )
    lines = manifest_to_lines(manifest)
    joined = "\n".join(lines)
    assert "omitted_items:" in joined
    assert "count: 3" in joined
    assert "reason: token_budget" in joined


# ── turn-history rolling window ────────────────────────────────


def _pairs(*nums: int) -> list[TurnPair]:
    return [TurnPair(user_body=f"u{n}", assistant_body=f"a{n}", turn_number=n) for n in nums]


def test_select_verbatim_window_short_history():
    older, recent = select_verbatim_window(_pairs(1, 2, 3))
    assert older == []
    assert [p.turn_number for p in recent] == [1, 2, 3]


def test_select_verbatim_window_keeps_last_k():
    older, recent = select_verbatim_window(_pairs(1, 2, 3, 4, 5, 6, 7))
    assert [p.turn_number for p in recent] == [4, 5, 6, 7]
    assert [p.turn_number for p in older] == [1, 2, 3]


def test_select_verbatim_window_respects_custom_k():
    older, recent = select_verbatim_window(_pairs(1, 2, 3, 4, 5), k=2)
    assert [p.turn_number for p in recent] == [4, 5]
    assert [p.turn_number for p in older] == [1, 2, 3]


def test_verbatim_window_k_default_matches_plan():
    assert TURN_HISTORY_VERBATIM_K == 4


def test_should_rebuild_summary_when_window_slides():
    """The cache stored a summary through turn-3; new older pair at
    turn-5 means the window slid and the summary needs refresh."""
    assert should_rebuild_summary(3, _pairs(5))
    assert not should_rebuild_summary(5, _pairs(5))
    assert not should_rebuild_summary(5, [])
