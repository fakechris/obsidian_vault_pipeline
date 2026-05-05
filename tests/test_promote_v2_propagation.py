"""Tests for BL-058a — promote path preserves v2 fields.

The BL-058 PR (#157) shipped a v2 absorb prompt that produces
``CandidateUnit`` JSON with ``unit_type`` / ``epistemic_role`` /
``source_anchor`` / ``specifics`` / ``related_concepts``.  But
``write_candidate_file`` and ``write_evergreen_file`` in
``promote_candidates.py`` were NOT updated as part of #157, so v2
extraction fields silently dropped on the candidate→evergreen
handoff.  The first incremental run after #157 produced ~60 evergreens
with v1 schema despite v2 LLM having run.

This test suite pins the v2-preservation contract end-to-end so the
bug can't recur.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from ovp_pipeline.concept_registry import ConceptEntry
from ovp_pipeline.promote_candidates import (
    _candidate_to_evergreen_v2,
    _looks_like_v2_concept,
    _render_v2_candidate,
    write_candidate_file,
    write_evergreen_file,
)


# ---------------------------------------------------------------------------
# v2 detection
# ---------------------------------------------------------------------------


class TestV2ConceptDetection:
    def test_unit_type_in_vocab_triggers_v2(self):
        for ut in ("fact", "method", "tradeoff", "failure_mode", "case_detail"):
            assert _looks_like_v2_concept({"unit_type": ut})

    def test_unit_type_outside_vocab_does_not_trigger(self):
        # "concept" is the v1 catchall, NOT in the v2 vocab
        assert not _looks_like_v2_concept({"unit_type": "concept"})
        assert not _looks_like_v2_concept({"unit_type": ""})

    def test_source_anchor_alone_triggers_v2(self):
        assert _looks_like_v2_concept({"source_anchor": "verbatim quote"})
        assert not _looks_like_v2_concept({"source_anchor": ""})

    def test_specifics_alone_triggers_v2(self):
        assert _looks_like_v2_concept({"specifics": ["numbers", "names"]})
        assert not _looks_like_v2_concept({"specifics": []})
        assert not _looks_like_v2_concept({"specifics": None})

    def test_pure_v1_concept_does_not_trigger(self):
        # The exact shape v1 callers produce — no v2 fields anywhere
        v1 = {
            "concept_name": "x",
            "title": "X",
            "explanation": "...",
            "importance": "...",
            "related_concepts": ["a", "b", "c"],
        }
        assert not _looks_like_v2_concept(v1)


# ---------------------------------------------------------------------------
# v2 candidate rendering
# ---------------------------------------------------------------------------


class TestV2CandidateRendering:
    def _entry(self) -> ConceptEntry:
        return ConceptEntry(
            slug="pid-lock-ownership",
            title="PID-based ownership prevents OOM-orphaned locks",
            definition="",
            area="general",
            aliases=["pid-lock-ownership"],
            kind="concept",
            review_state="auto",
        )

    def _v2_concept(self) -> dict:
        return {
            "title": "PID-based ownership prevents OOM-orphaned locks",
            "unit_type": "method",
            "epistemic_role": "method",
            "explanation": "Bind lock ownership to PID lifecycle so OOM kill releases the lock.",
            "source_anchor": "lock ownership tied to PID lifecycle",
            "specifics": ["names", "examples", "edge_cases"],
            "related_concepts": ["distributed-locks", "k8s-oom"],
        }

    def test_candidate_carries_all_v2_frontmatter_fields(self, tmp_path):
        path = write_candidate_file(
            tmp_path,
            self._entry(),
            dry_run=False,
            concept_data=self._v2_concept(),
            source_file=tmp_path / "fake-source.md",
        )
        content = path.read_text(encoding="utf-8")
        assert "extraction_prompt_version: v2" in content
        assert "unit_type: method" in content
        assert "epistemic_role: method" in content
        # _yaml_quote only adds quotes when the value has YAML-special chars
        assert "source_anchor: lock ownership tied to PID lifecycle" in content
        assert "specifics: [names, examples, edge_cases]" in content
        assert "related_concepts: [distributed-locks, k8s-oom]" in content
        # absorbed_at present and looks like an ISO-8601 UTC timestamp
        assert 'absorbed_at: "20' in content and 'Z"' in content

    def test_candidate_body_has_no_v1_template_sections(self, tmp_path):
        path = write_candidate_file(
            tmp_path, self._entry(),
            dry_run=False,
            concept_data=self._v2_concept(),
            source_file=tmp_path / "fake-source.md",
        )
        content = path.read_text(encoding="utf-8")
        assert "> **定义**" not in content
        assert "## 📝 详细解释" not in content
        assert "## 为什么重要" not in content
        # v2 body markers ARE present
        assert "## Related" in content
        assert '> **Source anchor**: "lock ownership tied to PID lifecycle"' in content

    def test_candidate_omits_related_section_when_empty(self, tmp_path):
        concept = self._v2_concept()
        concept["related_concepts"] = []
        path = write_candidate_file(
            tmp_path, self._entry(),
            dry_run=False, concept_data=concept,
            source_file=tmp_path / "fake-source.md",
        )
        content = path.read_text(encoding="utf-8")
        assert "## Related" not in content
        assert "related_concepts: []" in content

    def test_v1_concept_falls_back_to_v1_template(self, tmp_path):
        v1_concept = {
            "concept_name": "old-style",
            "title": "Old Style",
            "one_sentence_def": "An old-style concept.",
            "explanation": "Lots of details here.",
            "importance": "Important because of reasons.",
            "related_concepts": ["a", "b", "c"],
        }
        path = write_candidate_file(
            tmp_path, self._entry(),
            dry_run=False, concept_data=v1_concept,
            source_file=tmp_path / "fake-source.md",
        )
        content = path.read_text(encoding="utf-8")
        # v1 markers still present for backward compat
        assert "> **定义**" in content
        assert "## 📝 详细解释" in content
        # v2 markers absent
        assert "extraction_prompt_version: v2" not in content
        assert "source_anchor:" not in content


# ---------------------------------------------------------------------------
# Candidate → evergreen handoff (v2 preservation)
# ---------------------------------------------------------------------------


class TestCandidateToEvergreenV2:
    def _v2_candidate_text(self) -> str:
        return """---
note_id: pid-lock-ownership
title: "PID-based ownership prevents OOM-orphaned locks"
type: candidate
entity_type: method
unit_type: method
epistemic_role: method
extraction_prompt_version: v2
absorbed_at: "2026-05-05T14:00:00Z"
date: 2026-05-05
tags: [candidate, general]
aliases: ["pid-lock-ownership"]
area: general
review_state: auto
source_anchor: "lock ownership tied to PID lifecycle"
specifics: [names, examples]
related_concepts: [distributed-locks]
---

# PID-based ownership prevents OOM-orphaned locks

Bind lock ownership to PID lifecycle so OOM kill releases the lock.

> **Source anchor**: "lock ownership tied to PID lifecycle"

## Related

- [[distributed-locks]]

## Source

- [[2026-05-05_some-source]]

---

*Candidate concept - pending review*
"""

    def _entry(self) -> ConceptEntry:
        return ConceptEntry(
            slug="pid-lock-ownership",
            title="PID-based ownership prevents OOM-orphaned locks",
            definition="",
            area="general",
            aliases=["pid-lock-ownership"],
            kind="method",
            review_state="auto",
        )

    def test_evergreen_inherits_v2_fields(self):
        text = _candidate_to_evergreen_v2(self._v2_candidate_text(), self._entry())
        assert "extraction_prompt_version: v2" in text
        assert "unit_type: method" in text
        assert "epistemic_role: method" in text
        assert 'source_anchor: "lock ownership tied to PID lifecycle"' in text
        assert "specifics: [names, examples]" in text
        assert "related_concepts: [distributed-locks]" in text
        # Body preserved
        assert '> **Source anchor**: "lock ownership tied to PID lifecycle"' in text
        assert "[[distributed-locks]]" in text

    def test_type_flipped_to_evergreen(self):
        text = _candidate_to_evergreen_v2(self._v2_candidate_text(), self._entry())
        assert "type: evergreen" in text
        # No leftover ``type: candidate``
        assert "type: candidate" not in text

    def test_review_state_dropped(self):
        text = _candidate_to_evergreen_v2(self._v2_candidate_text(), self._entry())
        assert "review_state:" not in text

    def test_tags_swap_candidate_for_evergreen(self):
        text = _candidate_to_evergreen_v2(self._v2_candidate_text(), self._entry())
        # tag list contains evergreen, not candidate
        import re
        tags_match = re.search(r"^tags:\s*\[(.+)\]\s*$", text, re.MULTILINE)
        assert tags_match is not None
        tags = [t.strip().strip('"\'') for t in tags_match.group(1).split(",")]
        assert "candidate" not in tags
        assert "evergreen" in tags
        assert "general" in tags  # area tag preserved

    def test_footer_swaps_to_promoted(self):
        text = _candidate_to_evergreen_v2(self._v2_candidate_text(), self._entry())
        assert "*Candidate concept - pending review*" not in text
        assert "*Promoted from candidate on" in text


# ---------------------------------------------------------------------------
# End-to-end: write_evergreen_file routes v2 candidate to v2 path
# ---------------------------------------------------------------------------


class TestWriteEvergreenFileRouting:
    def test_v2_candidate_produces_v2_evergreen(self, tmp_path):
        # Build a vault skeleton + a v2 candidate file
        for d in ["10-Knowledge/Evergreen", "10-Knowledge/Evergreen/_Candidates"]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
        candidate_path = (
            tmp_path / "10-Knowledge" / "Evergreen" / "_Candidates" / "pid-lock-ownership.md"
        )
        candidate_path.write_text(
            TestCandidateToEvergreenV2()._v2_candidate_text(),
            encoding="utf-8",
        )

        entry = ConceptEntry(
            slug="pid-lock-ownership",
            title="PID-based ownership prevents OOM-orphaned locks",
            definition="",
            area="general",
            aliases=["pid-lock-ownership"],
            kind="method",
            review_state="auto",
        )

        evergreen_path = write_evergreen_file(tmp_path, entry, dry_run=False)
        assert evergreen_path is not None
        text = evergreen_path.read_text(encoding="utf-8")
        # All v2 fields preserved
        assert "extraction_prompt_version: v2" in text
        assert "unit_type: method" in text
        assert 'source_anchor: "lock ownership tied to PID lifecycle"' in text
        # type flipped, review_state dropped, footer swapped
        assert "type: evergreen" in text
        assert "review_state:" not in text
        assert "*Promoted from candidate on" in text

    def test_v1_candidate_still_uses_legacy_template(self, tmp_path):
        """Sanity check the v1 backward-compat path still works for
        callers that don't yet produce v2 candidates."""
        for d in ["10-Knowledge/Evergreen", "10-Knowledge/Evergreen/_Candidates"]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
        candidate_path = (
            tmp_path / "10-Knowledge" / "Evergreen" / "_Candidates" / "old-style.md"
        )
        candidate_path.write_text(
            "---\n"
            "note_id: old-style\n"
            'title: "Old Style"\n'
            "type: candidate\n"
            "entity_type: concept\n"
            "date: 2026-04-01\n"
            "tags: [candidate, general]\n"
            "aliases: [\"old-style\"]\n"
            "area: general\n"
            "review_state: auto\n"
            "---\n\n"
            "# Old Style\n\n"
            "> **定义**: An old-style concept.\n\n"
            "## 📝 详细解释\nLots of details.\n\n"
            "---\n\n"
            "*Candidate concept - pending review*\n",
            encoding="utf-8",
        )

        entry = ConceptEntry(
            slug="old-style",
            title="Old Style",
            definition="An old-style concept.",
            area="general",
            aliases=["old-style"],
            kind="concept",
            review_state="auto",
        )
        evergreen_path = write_evergreen_file(tmp_path, entry, dry_run=False)
        text = evergreen_path.read_text(encoding="utf-8")
        # v1 evergreen template — no v2 markers
        assert "extraction_prompt_version" not in text
        assert "source_anchor" not in text
        # v1 markers ARE present
        assert "type: evergreen" in text
        assert "*Promoted from candidate on" in text
