"""Tests for ``ovp-backfill-entity-type`` (BL-030).

The CLI now runs in two phases:

  * **Phase 1** — deterministic ``unit_type → entity_type`` rewrite for
    v2 evergreens whose ``entity_type`` was set by the pre-BL-025
    collapse but whose ``unit_type`` carries the real richer kind.
    No LLM call.
  * **Phase 2** — LLM classification for v1 evergreens with no
    recognised ``unit_type`` and no valid ``entity_type``.

These tests exercise Phase 1 end-to-end (no LLM dependency) and
verify the bucketing logic stays correct for the mixed cases.
"""

from __future__ import annotations

import json
from pathlib import Path

from ovp_pipeline.commands.backfill_entity_type import run


def _write_evergreen(
    vault: Path, slug: str, frontmatter: str, body: str = "Body.\n",
) -> Path:
    ev_dir = vault / "10-Knowledge" / "Evergreen"
    ev_dir.mkdir(parents=True, exist_ok=True)
    f = ev_dir / f"{slug}.md"
    f.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return f


class TestPhase1V2Passthrough:
    """v2 evergreens with ``unit_type`` get rewritten to match
    deterministically — no LLM call required.
    """

    def test_collapsed_v2_evergreen_gets_rewritten(self, tmp_path):
        # Pre-BL-025 collapse: unit_type=fact but entity_type was
        # forced to concept.
        f = _write_evergreen(
            tmp_path,
            "x-fact",
            'title: "X"\nentity_type: concept\nunit_type: fact',
        )
        result = run(tmp_path, dry_run=False)
        assert result["phase1_count"] == 1
        assert result["phase2_count"] == 0
        assert result["distribution"].get("fact") == 1
        # File rewritten.
        text = f.read_text("utf-8")
        assert "entity_type: fact" in text
        assert "unit_type: fact" in text

    def test_already_matching_v2_evergreen_skipped(self, tmp_path):
        # Post-BL-025 v2 evergreen — entity_type already matches
        # unit_type, no rewrite needed.
        f = _write_evergreen(
            tmp_path,
            "x-method",
            'title: "X"\nentity_type: method\nunit_type: method',
        )
        before = f.read_text("utf-8")
        result = run(tmp_path, dry_run=False)
        assert result["already_correct"] == 1
        assert result["phase1_count"] == 0
        # File unchanged.
        assert f.read_text("utf-8") == before

    def test_mixed_buckets_split_correctly(self, tmp_path):
        _write_evergreen(
            tmp_path, "a-collapsed",
            'title: "A"\nentity_type: concept\nunit_type: tradeoff',
        )
        _write_evergreen(
            tmp_path, "b-already",
            'title: "B"\nentity_type: procedure\nunit_type: procedure',
        )
        # v1 evergreen with concept set — already correct under
        # the conservative default (no LLM re-classify of "concept"
        # set without unit_type).
        _write_evergreen(
            tmp_path, "c-v1",
            'title: "C"\nentity_type: concept',
        )
        result = run(tmp_path, dry_run=False)
        assert result["phase1_count"] == 1
        assert result["already_correct"] == 2
        assert result["phase2_count"] == 0


class TestDryRun:
    def test_dry_run_doesnt_write(self, tmp_path):
        f = _write_evergreen(
            tmp_path, "x",
            'title: "X"\nentity_type: concept\nunit_type: learning',
        )
        before = f.read_text("utf-8")
        result = run(tmp_path, dry_run=True)
        assert result["dry_run"] is True
        assert result["phase1"] == 1
        # File untouched.
        assert f.read_text("utf-8") == before


class TestUnknownUnitTypeFallsThrough:
    def test_unrecognised_unit_type_routed_to_phase2(self, tmp_path):
        # Made-up ``unit_type`` doesn't match V2_UNIT_TYPES, so the
        # file falls through to Phase 2 (LLM).  With no entity_type
        # set, it goes into the LLM bucket.
        _write_evergreen(
            tmp_path, "x",
            'title: "X"\nunit_type: alien-kind',
        )
        result = run(tmp_path, dry_run=True)
        assert result["phase1"] == 0
        assert result["phase2"] == 1


class TestAuditEvents:
    def test_phase1_emits_passthrough_event(self, tmp_path):
        _write_evergreen(
            tmp_path, "x",
            'title: "X"\nentity_type: concept\nunit_type: failure_mode',
        )
        run(tmp_path, dry_run=False)
        log = (tmp_path / "60-Logs" / "pipeline.jsonl").read_text("utf-8")
        events = [json.loads(line) for line in log.splitlines() if line.strip()]
        passthrough = [e for e in events
                       if e.get("event_type") == "entity_type_backfill_v2_passthrough"]
        assert len(passthrough) == 1
        assert passthrough[0]["entity_type"] == "failure_mode"
        assert passthrough[0]["previous"] == "concept"
        # Summary event also recorded.
        summary = [e for e in events
                   if e.get("event_type") == "entity_type_backfill_summary"]
        assert len(summary) == 1
        assert summary[0]["phase1_count"] == 1


class TestSkippedNoFrontmatter:
    """An Evergreen markdown without a frontmatter block can't carry
    ``entity_type`` until someone adds frontmatter to it.  Pre-fix
    the CLI silently no-op'd, wrote the file back unchanged, and
    counted it as classified — the report claimed success while the
    file on disk still had no ``entity_type``.  Now those files are
    counted in their own bucket and audited as skipped."""

    def test_no_frontmatter_evergreen_counts_as_skipped(self, tmp_path):
        ev_dir = tmp_path / "10-Knowledge" / "Evergreen"
        ev_dir.mkdir(parents=True, exist_ok=True)
        plain = ev_dir / "plain.md"
        plain.write_text("# Plain note with no frontmatter\nBody only.\n", encoding="utf-8")
        before = plain.read_text("utf-8")

        result = run(tmp_path, dry_run=False)

        # Phase 2 picked it up (no entity_type, no unit_type).
        assert result["phase2_count"] == 1
        # But it bumps the new ``skipped_no_frontmatter`` counter and
        # is NOT counted as classified.
        assert result["skipped_no_frontmatter"] == 1
        assert result["classified"] == 0
        # File on disk is untouched.
        assert plain.read_text("utf-8") == before
        # Audit log gets the explicit skip event, not a fake backfill.
        log = (tmp_path / "60-Logs" / "pipeline.jsonl").read_text("utf-8")
        events = [json.loads(line) for line in log.splitlines() if line.strip()]
        skipped = [e for e in events
                   if e.get("event_type") == "entity_type_backfill_skipped"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "no_frontmatter"
        backfill_events = [e for e in events
                           if e.get("event_type") == "entity_type_backfill"]
        assert backfill_events == []
