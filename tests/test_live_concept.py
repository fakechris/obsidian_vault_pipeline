"""BL-063 PR#1: Live Concept data model + discovery + fileops.

Covers ``live_concept`` (parser + discovery) and
``live_concept_fileops`` (single-writer for the ``live:`` block).

PR#2 (triggers) and PR#3 (agent prompt) build on top of these
primitives — no triggers / agent calls exercised here.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# parse_live_concept_block — the YAML-block parser
# ---------------------------------------------------------------------------


def test_parse_block_minimal_objective_only():
    from ovp_pipeline.live_concept import parse_live_concept_block

    fm = parse_live_concept_block({"objective": "Track LLM evals."})
    assert fm is not None
    assert fm.objective == "Track LLM evals."
    assert fm.is_active is True
    assert fm.triggers == {}
    assert fm.scope_evergreens == ()


def test_parse_block_full_shape():
    from ovp_pipeline.live_concept import parse_live_concept_block

    fm = parse_live_concept_block({
        "objective": "Track LLM evals.",
        "active": True,
        "triggers": {
            "on_ingest_match": {"concept_similarity_to": "llm-eval"},
            "weekly_resynthesis": "Mon 09:00",
        },
        "scope_evergreens": ["llm-eval-leakage", "eval-cost-vs-quality"],
        "lastAttemptAt": "2026-05-10T08:00:00Z",
        "lastRunAt": "2026-05-10T08:00:01Z",
        "lastRunSummary": "Refreshed.",
        "lastRunError": "",
    })
    assert fm is not None
    assert fm.scope_evergreens == ("llm-eval-leakage", "eval-cost-vs-quality")
    assert fm.last_attempt_at == "2026-05-10T08:00:00Z"
    assert fm.triggers["on_ingest_match"]["concept_similarity_to"] == "llm-eval"


def test_parse_block_rejects_missing_objective():
    from ovp_pipeline.live_concept import parse_live_concept_block

    assert parse_live_concept_block({}) is None
    assert parse_live_concept_block({"objective": ""}) is None
    assert parse_live_concept_block({"objective": "   "}) is None
    assert parse_live_concept_block({"active": True}) is None


def test_parse_block_explicit_false_active_disables():
    """``active: false`` is the documented escape hatch — the
    scheduler must skip it."""
    from ovp_pipeline.live_concept import parse_live_concept_block

    fm = parse_live_concept_block({"objective": "x", "active": False})
    assert fm is not None
    assert fm.is_active is False


def test_parse_block_null_active_disables():
    """``active: null`` is the YAML way to say "explicitly empty";
    same as False — disables."""
    from ovp_pipeline.live_concept import parse_live_concept_block

    fm = parse_live_concept_block({"objective": "x", "active": None})
    assert fm is not None
    assert fm.is_active is False


def test_parse_block_missing_active_defaults_true():
    """When the key isn't there at all → active (the common case
    after a fresh `set_live`)."""
    from ovp_pipeline.live_concept import parse_live_concept_block

    fm = parse_live_concept_block({"objective": "x"})
    assert fm is not None
    assert fm.is_active is True


def test_parse_block_handles_single_string_scope():
    """YAML ``scope_evergreens: foo`` (string instead of list) is
    coerced to a one-tuple — be liberal on shape, conservative on
    field names."""
    from ovp_pipeline.live_concept import parse_live_concept_block

    fm = parse_live_concept_block({
        "objective": "x",
        "scope_evergreens": "single-evergreen-slug",
    })
    assert fm is not None
    assert fm.scope_evergreens == ("single-evergreen-slug",)


def test_parse_block_drops_blank_scope_entries():
    from ovp_pipeline.live_concept import parse_live_concept_block

    fm = parse_live_concept_block({
        "objective": "x",
        "scope_evergreens": ["alpha", "", "beta", None, "  gamma  "],
    })
    assert fm is not None
    assert fm.scope_evergreens == ("alpha", "beta", "gamma")


def test_parse_block_rejects_non_dict_inputs():
    from ovp_pipeline.live_concept import parse_live_concept_block

    assert parse_live_concept_block(None) is None
    assert parse_live_concept_block("not a dict") is None
    assert parse_live_concept_block(["list"]) is None


# ---------------------------------------------------------------------------
# parse_live_concept — full file path
# ---------------------------------------------------------------------------


def _write_concept_file(tmp_path, slug, frontmatter_yaml, body="\n# Test body\n"):
    path = tmp_path / "30-Projects" / "Tracking" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"---\n{frontmatter_yaml.strip()}\n---\n{body}"
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_full_file_returns_handle(tmp_path):
    from ovp_pipeline.live_concept import parse_live_concept

    path = _write_concept_file(
        tmp_path, "llm-eval",
        """type: live-concept
live:
  objective: Track LLM evals.
  active: true
  scope_evergreens:
    - llm-eval-leakage""",
    )
    handle = parse_live_concept(path)
    assert handle is not None
    assert handle.slug == "llm-eval"
    assert handle.relative_path == "30-Projects/Tracking/llm-eval.md"
    assert handle.frontmatter.scope_evergreens == ("llm-eval-leakage",)


def test_parse_rejects_file_without_type_marker(tmp_path):
    """Frontmatter has a ``live:`` block but no ``type: live-concept``
    — could be any other note type accidentally using the key.
    Refuse to claim it."""
    from ovp_pipeline.live_concept import parse_live_concept

    path = _write_concept_file(
        tmp_path, "notmine",
        """type: evergreen
live:
  objective: this should not be treated as a live concept""",
    )
    assert parse_live_concept(path) is None


def test_parse_rejects_file_without_live_block(tmp_path):
    from ovp_pipeline.live_concept import parse_live_concept

    path = _write_concept_file(
        tmp_path, "noliveblock",
        "type: live-concept",
    )
    assert parse_live_concept(path) is None


def test_parse_rejects_file_with_no_frontmatter(tmp_path):
    from ovp_pipeline.live_concept import parse_live_concept

    path = tmp_path / "30-Projects" / "Tracking" / "no-fm.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Plain markdown\n\nNo frontmatter here.\n", encoding="utf-8")
    assert parse_live_concept(path) is None


def test_parse_returns_none_for_missing_file(tmp_path):
    from ovp_pipeline.live_concept import parse_live_concept

    assert parse_live_concept(tmp_path / "nonexistent.md") is None


# ---------------------------------------------------------------------------
# list_live_concepts — discovery
# ---------------------------------------------------------------------------


def test_list_walks_tracking_directory(tmp_path):
    from ovp_pipeline.live_concept import list_live_concepts

    _write_concept_file(tmp_path, "concept-a",
        "type: live-concept\nlive:\n  objective: A")
    _write_concept_file(tmp_path, "concept-b",
        "type: live-concept\nlive:\n  objective: B")

    handles = list_live_concepts(tmp_path)
    slugs = sorted(h.slug for h in handles)
    assert slugs == ["concept-a", "concept-b"]


def test_list_skips_non_live_concept_files(tmp_path):
    """Tracking directory may contain unrelated .md files (drafts,
    indexes); discovery silently ignores them."""
    from ovp_pipeline.live_concept import list_live_concepts

    _write_concept_file(tmp_path, "live-one",
        "type: live-concept\nlive:\n  objective: yes")
    # A regular note in the same dir — must NOT appear in results.
    plain = tmp_path / "30-Projects" / "Tracking" / "draft.md"
    plain.write_text("---\ntype: note\n---\n\nDraft body.\n", encoding="utf-8")

    handles = list_live_concepts(tmp_path)
    assert [h.slug for h in handles] == ["live-one"]


def test_list_returns_empty_on_missing_directory(tmp_path):
    from ovp_pipeline.live_concept import list_live_concepts

    # No 30-Projects/Tracking/ dir at all → empty list, no exception.
    assert list_live_concepts(tmp_path) == []


def test_list_active_only_filters_passive(tmp_path):
    from ovp_pipeline.live_concept import list_live_concepts

    _write_concept_file(tmp_path, "alive",
        "type: live-concept\nlive:\n  objective: alive\n  active: true")
    _write_concept_file(tmp_path, "paused",
        "type: live-concept\nlive:\n  objective: paused\n  active: false")

    all_handles = list_live_concepts(tmp_path)
    assert sorted(h.slug for h in all_handles) == ["alive", "paused"]

    active_only = list_live_concepts(tmp_path, active_only=True)
    assert [h.slug for h in active_only] == ["alive"]


# ---------------------------------------------------------------------------
# fileops: set_live / patch_live / delete_live
# ---------------------------------------------------------------------------


def test_set_live_creates_file_when_missing(tmp_path):
    from ovp_pipeline.live_concept import (
        LiveConceptFrontmatter,
        parse_live_concept,
    )
    from ovp_pipeline.live_concept_fileops import set_live

    path = tmp_path / "30-Projects" / "Tracking" / "fresh.md"
    set_live(
        path,
        LiveConceptFrontmatter(
            objective="Track LLM evals.",
            scope_evergreens=("eval-cost-vs-quality",),
        ),
    )
    handle = parse_live_concept(path)
    assert handle is not None
    assert handle.frontmatter.objective == "Track LLM evals."
    assert handle.frontmatter.scope_evergreens == ("eval-cost-vs-quality",)
    # Stub body materialised so Obsidian shows something meaningful.
    text = path.read_text(encoding="utf-8")
    assert "# Fresh" in text


def test_set_live_preserves_other_frontmatter_keys(tmp_path):
    """When the file already has frontmatter (e.g. ``aliases``,
    ``tags``), set_live must leave those keys alone — only touches
    ``type`` and ``live``."""
    from ovp_pipeline.live_concept import (
        LiveConceptFrontmatter,
        parse_live_concept,
    )
    from ovp_pipeline.live_concept_fileops import set_live

    path = tmp_path / "30-Projects" / "Tracking" / "existing.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntags:\n  - llm\n  - eval\naliases:\n  - LLM Eval\n---\n\n"
        "# Existing\n\nBody preserved.\n",
        encoding="utf-8",
    )
    set_live(path, LiveConceptFrontmatter(objective="Track."))

    text = path.read_text(encoding="utf-8")
    assert "tags:" in text and "llm" in text and "eval" in text
    assert "aliases:" in text and "LLM Eval" in text
    assert "Body preserved." in text
    handle = parse_live_concept(path)
    assert handle is not None and handle.frontmatter.objective == "Track."


def test_patch_live_updates_subset_keeps_rest(tmp_path):
    from ovp_pipeline.live_concept import (
        LiveConceptFrontmatter,
        parse_live_concept,
    )
    from ovp_pipeline.live_concept_fileops import patch_live, set_live

    path = tmp_path / "30-Projects" / "Tracking" / "patchable.md"
    set_live(
        path,
        LiveConceptFrontmatter(
            objective="Track LLM evals.",
            scope_evergreens=("a", "b"),
        ),
    )
    new_fm = patch_live(
        path,
        last_attempt_at="2026-05-10T08:00:00Z",
        last_run_at="2026-05-10T08:00:01Z",
        last_run_summary="Refreshed.",
    )
    # Patched fields updated.
    assert new_fm.last_attempt_at == "2026-05-10T08:00:00Z"
    assert new_fm.last_run_summary == "Refreshed."
    # User-edited fields preserved.
    assert new_fm.objective == "Track LLM evals."
    assert new_fm.scope_evergreens == ("a", "b")
    # Disk read-back agrees.
    handle = parse_live_concept(path)
    assert handle is not None
    assert handle.frontmatter.last_run_summary == "Refreshed."


def test_patch_live_rejects_unknown_field(tmp_path):
    from ovp_pipeline.live_concept import LiveConceptFrontmatter
    from ovp_pipeline.live_concept_fileops import patch_live, set_live

    path = tmp_path / "30-Projects" / "Tracking" / "x.md"
    set_live(path, LiveConceptFrontmatter(objective="x"))

    with pytest.raises(TypeError, match=r"valid"):
        patch_live(path, lastAttemptAt="2026-05-10")  # camelCase typo


def test_patch_live_raises_on_missing_block(tmp_path):
    from ovp_pipeline.live_concept_fileops import patch_live

    path = tmp_path / "30-Projects" / "Tracking" / "noblock.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: note\n---\n\n# No live block\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"no parseable"):
        patch_live(path, last_run_at="2026-05-10")


def test_delete_live_strips_block_keeps_body(tmp_path):
    from ovp_pipeline.live_concept import LiveConceptFrontmatter, parse_live_concept
    from ovp_pipeline.live_concept_fileops import delete_live, set_live

    path = tmp_path / "30-Projects" / "Tracking" / "doomed.md"
    set_live(path, LiveConceptFrontmatter(objective="x"))
    # Append a body the operator would have hand-written.
    body = "\n## My take\n\nMy real opinion lives here.\n"
    path.write_text(path.read_text(encoding="utf-8") + body, encoding="utf-8")

    delete_live(path)

    text = path.read_text(encoding="utf-8")
    assert "live:" not in text  # block stripped
    assert "type: live-concept" not in text  # marker stripped
    assert "My real opinion lives here." in text  # body preserved
    # And it no longer parses as a live concept.
    assert parse_live_concept(path) is None


def test_delete_live_idempotent(tmp_path):
    """No-op when the file isn't a live concept — delete twice is fine."""
    from ovp_pipeline.live_concept_fileops import delete_live

    path = tmp_path / "30-Projects" / "Tracking" / "plain.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Plain\n\nBody.\n", encoding="utf-8")
    delete_live(path)
    delete_live(path)  # second call also a no-op
    assert path.read_text(encoding="utf-8") == "# Plain\n\nBody.\n"


# ---------------------------------------------------------------------------
# Round-trip: set_live → parse_live_concept agrees
# ---------------------------------------------------------------------------


def test_round_trip_preserves_all_fields(tmp_path):
    from ovp_pipeline.live_concept import (
        LiveConceptFrontmatter,
        parse_live_concept,
    )
    from ovp_pipeline.live_concept_fileops import set_live

    original = LiveConceptFrontmatter(
        objective="A multi-line\nobjective with newlines.",
        active=True,
        triggers={
            "on_ingest_match": {
                "concept_similarity_to": "llm-eval",
                "threshold": 0.65,
            },
            "weekly_resynthesis": "Mon 09:00",
        },
        scope_evergreens=("alpha", "beta"),
        last_run_at="2026-05-10T08:00:01Z",
        last_run_summary="Synthesis refreshed.",
    )
    path = tmp_path / "30-Projects" / "Tracking" / "rt.md"
    set_live(path, original)
    handle = parse_live_concept(path)
    assert handle is not None
    rt = handle.frontmatter
    assert rt.objective == original.objective
    assert rt.scope_evergreens == original.scope_evergreens
    assert rt.triggers == original.triggers
    assert rt.last_run_at == original.last_run_at
    assert rt.last_run_summary == original.last_run_summary
