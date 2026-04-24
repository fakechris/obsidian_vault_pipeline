"""Tests for Phase 38.C — promotion-backlink writeback."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from ovp_pipeline.promotion_backlinks import (
    MARKER_CLOSE,
    MARKER_OPEN,
    list_promotions,
    upsert_promotions,
    upsert_promotions_in_file,
)


def test_list_promotions_returns_empty_when_no_block():
    text = "just some markdown\n\nwith [[a-wikilink]] outside any block\n"
    assert list_promotions(text) == []


def test_list_promotions_extracts_slugs_from_block():
    text = dedent(
        f"""\
        body text

        {MARKER_OPEN}
        > 由 OVP Pipeline 自动提取的 Evergreen 概念
        - [[Slug-One]]
        - [[Slug-Two]]
        {MARKER_CLOSE}
        """
    )
    assert list_promotions(text) == ["Slug-One", "Slug-Two"]


def test_upsert_promotions_creates_block_when_absent():
    text = "body content\n"
    new_text, changed = upsert_promotions(text, ["Concept-A"])
    assert changed is True
    assert MARKER_OPEN in new_text
    assert MARKER_CLOSE in new_text
    assert "[[Concept-A]]" in new_text
    # Block lands at the end, original body intact.
    assert new_text.startswith("body content\n")


def test_upsert_promotions_appends_new_slugs_to_existing_block():
    text = dedent(
        f"""\
        body
        {MARKER_OPEN}
        > 由 OVP Pipeline 自动提取的 Evergreen 概念
        - [[Existing-One]]
        {MARKER_CLOSE}
        """
    )
    new_text, changed = upsert_promotions(text, ["New-One"])
    assert changed is True
    slugs = list_promotions(new_text)
    assert slugs == ["Existing-One", "New-One"]


def test_upsert_promotions_idempotent_for_same_slug():
    text = "body\n"
    after_first, _ = upsert_promotions(text, ["Concept-A"])
    after_second, changed = upsert_promotions(after_first, ["Concept-A"])
    assert changed is False
    assert after_first == after_second


def test_upsert_promotions_dedupes_within_one_call():
    text = "body\n"
    new_text, _ = upsert_promotions(text, ["Concept-A", "Concept-A", "Concept-B"])
    assert list_promotions(new_text) == ["Concept-A", "Concept-B"]


def test_upsert_promotions_no_op_for_empty_input():
    text = "body\n"
    new_text, changed = upsert_promotions(text, [])
    assert changed is False
    assert new_text == text


def test_upsert_promotions_in_file_round_trip(tmp_path: Path):
    p = tmp_path / "source.md"
    p.write_text("# Source Note\n\nbody text\n", encoding="utf-8")
    changed = upsert_promotions_in_file(p, ["Concept-Alpha", "Concept-Beta"])
    assert changed is True
    content = p.read_text(encoding="utf-8")
    assert list_promotions(content) == ["Concept-Alpha", "Concept-Beta"]


def test_upsert_promotions_in_file_missing_returns_false(tmp_path: Path):
    p = tmp_path / "missing.md"
    assert upsert_promotions_in_file(p, ["X"]) is False


def test_block_replaces_in_place_does_not_duplicate(tmp_path: Path):
    p = tmp_path / "source.md"
    p.write_text("body\n", encoding="utf-8")
    upsert_promotions_in_file(p, ["A"])
    upsert_promotions_in_file(p, ["B"])
    content = p.read_text(encoding="utf-8")
    # Only one block in the file.
    assert content.count(MARKER_OPEN) == 1
    assert content.count(MARKER_CLOSE) == 1
    assert list_promotions(content) == ["A", "B"]
