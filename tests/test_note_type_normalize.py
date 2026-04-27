"""Tests for Phase 38.D — note_type normalization."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from ovp_pipeline.note_type_normalize import (
    CANONICAL_NOTE_TYPES,
    NormalizationMapping,
    apply_normalization,
    load_mapping,
    plan_normalization,
    rewrite_note_type,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


def test_canonical_set_contains_eight_values():
    assert CANONICAL_NOTE_TYPES == frozenset(
        {"raw", "deep_dive", "evergreen", "moc", "daily_view", "article", "project", "essay"}
    )


def test_load_default_mapping_covers_known_legacy_types():
    mapping = load_mapping()
    # A handful of representative legacy types from the live vault.
    assert mapping.normalize("engineering-blog-post") == "article"
    assert mapping.normalize("github-project") == "project"
    assert mapping.normalize("ai-marketing-automation") == "article"
    assert mapping.normalize("ai-safety-essay") == "essay"
    assert mapping.normalize("技术分析") == "article"
    assert mapping.normalize("Threat Intelligence Report") == "article"


def test_normalize_passes_through_canonical_values():
    mapping = load_mapping()
    for value in CANONICAL_NOTE_TYPES:
        assert mapping.normalize(value) == value


def test_normalize_unknown_value_falls_back_to_article():
    mapping = NormalizationMapping(mapping={}, extras=frozenset())
    assert mapping.normalize("totally-unknown-thing") == "article"


def test_extras_act_as_canonical():
    mapping = NormalizationMapping(mapping={}, extras=frozenset({"pack-special"}))
    assert mapping.normalize("pack-special") == "pack-special"


def test_rewrite_note_type_replaces_type_field_and_records_original():
    text = dedent(
        """\
        ---
        title: "X"
        type: engineering-blog-post
        ---

        body
        """
    )
    new_text, original = rewrite_note_type(text, new_value="article")
    assert original == "engineering-blog-post"
    assert "type: article" in new_text
    assert "original_note_type: engineering-blog-post" in new_text
    assert new_text.endswith("\nbody\n")


def test_rewrite_note_type_handles_quoted_values():
    text = dedent(
        """\
        ---
        title: X
        type: "ai-marketing-automation"
        ---

        body
        """
    )
    new_text, original = rewrite_note_type(text, new_value="article")
    assert original == "ai-marketing-automation"
    assert "type: article" in new_text


def test_rewrite_note_type_handles_note_type_alias():
    text = dedent(
        """\
        ---
        title: X
        note_type: ai-strategy
        ---

        body
        """
    )
    new_text, original = rewrite_note_type(text, new_value="article")
    assert original == "ai-strategy"
    assert "note_type: article" in new_text
    assert "original_note_type: ai-strategy" in new_text


def test_rewrite_note_type_normalizes_note_type_even_when_type_is_canonical():
    text = dedent(
        """\
        ---
        title: X
        type: article
        note_type: technical-analysis
        ---

        body
        """
    )

    new_text, original = rewrite_note_type(text, new_value="article")

    assert original == "technical-analysis"
    assert "type: article" in new_text
    assert "note_type: article" in new_text
    assert "original_note_type: technical-analysis" in new_text


def test_rewrite_note_type_handles_fenced_yaml_frontmatter():
    text = dedent(
        """\
        ```yaml
        ---
        title: X
        type: technical-analysis
        ---
        ```

        body
        """
    )

    new_text, original = rewrite_note_type(text, new_value="article")

    assert original == "technical-analysis"
    assert new_text.startswith("```yaml\n---\n")
    assert "type: article" in new_text
    assert "original_note_type: technical-analysis" in new_text
    assert "```\n\nbody" in new_text


def test_rewrite_note_type_handles_unclosed_fenced_yaml_frontmatter():
    text = dedent(
        """\
        ```yaml
        ---
        title: X
        type: github-project
        ---

        # Body starts without closing fence
        """
    )

    new_text, original = rewrite_note_type(text, new_value="project")

    assert original == "github-project"
    assert new_text.startswith("```yaml\n---\n")
    assert "type: project" in new_text
    assert "original_note_type: github-project" in new_text
    assert "# Body starts without closing fence" in new_text


def test_rewrite_note_type_no_change_when_already_canonical():
    text = dedent(
        """\
        ---
        title: X
        type: article
        ---

        body
        """
    )
    new_text, original = rewrite_note_type(text, new_value="article")
    assert new_text == text
    assert original == "article"


def test_rewrite_note_type_no_frontmatter_returns_unchanged():
    text = "no frontmatter here\n"
    new_text, original = rewrite_note_type(text, new_value="article")
    assert new_text == text
    assert original is None


def test_plan_normalization_walks_vault(tmp_path: Path):
    _write(
        tmp_path / "20-Areas/topic.md",
        """\
        ---
        title: Topic
        type: engineering-blog-post
        ---

        body
        """,
    )
    _write(
        tmp_path / "10-Knowledge/Evergreen/concept.md",
        """\
        ---
        title: Concept
        type: evergreen
        ---

        body
        """,
    )
    _write(
        tmp_path / "20-Areas/_template.md",
        """\
        ---
        type: anything
        ---
        """,
    )
    _write(
        tmp_path / "20-Areas/fenced.md",
        """\
        ```yaml
        ---
        title: Fenced
        type: technical-analysis
        ---
        ```
        """,
    )

    report = plan_normalization(tmp_path, load_mapping())
    paths_changed = {c.path.name for c in report.changed}
    assert paths_changed == {"topic.md", "fenced.md"}
    assert any(c.path.name == "concept.md" for c in report.skipped)
    # Template (underscore prefix) is excluded entirely.
    assert not any("_template" in str(c.path) for c in report.changed)


def test_apply_normalization_writes_files(tmp_path: Path):
    target = tmp_path / "20-Areas/topic.md"
    _write(
        target,
        """\
        ---
        title: Topic
        type: engineering-blog-post
        date: 2026-04-23
        ---

        Some body text.
        """,
    )

    report = apply_normalization(tmp_path, load_mapping(), dry_run=False)
    assert len(report.changed) == 1
    new_text = target.read_text(encoding="utf-8")
    assert "type: article" in new_text
    assert "original_note_type: engineering-blog-post" in new_text
    assert "Some body text." in new_text


def test_apply_normalization_dry_run_makes_no_writes(tmp_path: Path):
    target = tmp_path / "20-Areas/topic.md"
    original = dedent(
        """\
        ---
        title: Topic
        type: engineering-blog-post
        ---

        body
        """
    )
    _write(target, original)

    report = apply_normalization(tmp_path, load_mapping(), dry_run=True)
    assert len(report.changed) == 1
    assert target.read_text(encoding="utf-8") == original


def test_apply_is_idempotent(tmp_path: Path):
    target = tmp_path / "20-Areas/topic.md"
    _write(
        target,
        """\
        ---
        title: Topic
        type: engineering-blog-post
        ---

        body
        """,
    )

    apply_normalization(tmp_path, load_mapping(), dry_run=False)
    after_first = target.read_text(encoding="utf-8")
    second_report = apply_normalization(tmp_path, load_mapping(), dry_run=False)
    assert second_report.changed == []
    assert target.read_text(encoding="utf-8") == after_first
