"""Tests for BL-058 — absorb v2 prompt + CandidateUnit schema.

Covers the parser, the unit→concept converter, the new evergreen body
template, and the legacy-tagging command.

We don't run the LLM here — the prompt itself is verified by the
6-source A/B experiment.  These tests pin behavioral contracts so a
future schema drift surfaces as a test failure, not as a silently
mis-rendered evergreen on disk.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ovp_pipeline.auto_evergreen_extractor import (
    EvergreenExtractor,
    PipelineLogger,
)
from ovp_pipeline.commands.tag_legacy_evergreens import (
    _classify_and_tag,
    _split_frontmatter,
    _untag_file,
    main as tag_legacy_main,
)


# ---------------------------------------------------------------------------
# v2 response parser
# ---------------------------------------------------------------------------


def _make_extractor(tmp_path: Path) -> EvergreenExtractor:
    log = PipelineLogger(tmp_path / "pipeline.jsonl")
    llm = MagicMock()
    return EvergreenExtractor(llm_client=llm, logger=log, vault_dir=tmp_path)


class TestV2ResponseParsing:
    def test_well_formed_units_array(self, tmp_path):
        extractor = _make_extractor(tmp_path)
        response = json.dumps({
            "source_value_summary": "Tweet about specific PID lock implementation",
            "units": [
                {
                    "slug": "pid-lock-ownership",
                    "title": "PID-based ownership prevents OOM-orphaned locks",
                    "unit_type": "method",
                    "epistemic_role": "method",
                    "content": "Bind lock ownership to PID lifecycle so OOM kill releases the lock. Used in their K8s cluster after Redis lock got stuck.",
                    "source_anchor": "lock ownership tied to PID lifecycle",
                    "specifics": ["names", "examples", "edge_cases"],
                    "related_concepts": ["distributed-locks", "k8s-oom"],
                },
                {
                    "slug": "redis-lock-orphaned-on-oom",
                    "title": "Redis distributed lock can stay held when holder is OOM-killed",
                    "unit_type": "failure_mode",
                    "epistemic_role": "fact",
                    "content": "A pod holding a Redis SET-NX lock that gets OOM-killed leaves the lock until the configured TTL expires.",
                    "source_anchor": "Redis lock didn't release on OOM",
                    "specifics": ["edge_cases"],
                    "related_concepts": [],
                },
            ],
            "skip_reason": "",
        })
        concepts = extractor._parse_v2_response(response, Path("/fake/source.md"))
        assert len(concepts) == 2
        c0 = concepts[0]
        assert c0["concept_name"] == "pid-lock-ownership"
        assert c0["title"].startswith("PID-based")
        assert c0["unit_type"] == "method"
        assert c0["epistemic_role"] == "method"
        assert c0["entity_type"] == "method"  # method/procedure → KIND_METHOD
        assert c0["source_anchor"] == "lock ownership tied to PID lifecycle"
        assert c0["specifics"] == ["names", "examples", "edge_cases"]
        assert c0["related_concepts"] == ["distributed-locks", "k8s-oom"]
        # Legacy fields preserved as empty so downstream renderers don't crash
        assert c0["one_sentence_def"] == ""
        assert c0["importance"] == ""
        # ``content`` lives under ``explanation`` for legacy-callers
        assert "OOM" in c0["explanation"]

    def test_skip_reason_empty_units_returns_empty_list(self, tmp_path):
        extractor = _make_extractor(tmp_path)
        response = json.dumps({
            "source_value_summary": "13 chars of repo name only",
            "units": [],
            "skip_reason": "源文仅含 repo 标题,无可抽取具体物",
        })
        concepts = extractor._parse_v2_response(response, Path("/fake/stub.md"))
        assert concepts == []

    def test_markdown_fenced_json_is_unwrapped(self, tmp_path):
        extractor = _make_extractor(tmp_path)
        wrapped = "```json\n" + json.dumps({"units": [], "skip_reason": "ok"}) + "\n```"
        concepts = extractor._parse_v2_response(wrapped, Path("/fake/x.md"))
        assert concepts == []

    def test_bare_list_response_is_rejected(self, tmp_path):
        """v1 prompt returned a bare list — v2 must NOT silently accept
        that, because the v1 fields aren't compatible with the new
        renderer."""
        extractor = _make_extractor(tmp_path)
        response = json.dumps([
            {"concept_name": "foo", "title": "Foo", "one_sentence_def": "..."},
        ])
        concepts = extractor._parse_v2_response(response, Path("/fake/x.md"))
        assert concepts == []

    def test_invalid_json_returns_empty(self, tmp_path):
        extractor = _make_extractor(tmp_path)
        concepts = extractor._parse_v2_response("not valid json", Path("/fake/x.md"))
        assert concepts == []

    def test_tolerates_conversational_filler_around_json(self, tmp_path):
        """gemini PR #157 review fix: even though we instruct the LLM
        not to wrap output in markdown, models occasionally emit
        ``好的,这是 JSON 输出: {...}`` or ``Here is the result:\\n{...}``.
        The parser must locate the JSON object inside the response
        rather than fail on the surrounding prose."""
        extractor = _make_extractor(tmp_path)
        wrapped = (
            "好的,这是按照你要求的 JSON:\n"
            + json.dumps({
                "units": [
                    {"slug": "ok", "title": "Ok claim", "unit_type": "fact"}
                ],
                "skip_reason": "",
            })
            + "\n\n希望对你有帮助!"
        )
        concepts = extractor._parse_v2_response(wrapped, Path("/fake/x.md"))
        assert len(concepts) == 1
        assert concepts[0]["concept_name"] == "ok"

    def test_no_json_object_in_response_returns_empty(self, tmp_path):
        """When the LLM returns prose with no JSON at all, parser
        should log + return [] (not crash)."""
        extractor = _make_extractor(tmp_path)
        concepts = extractor._parse_v2_response("Just a sentence with no json.", Path("/fake/x.md"))
        assert concepts == []

    def test_unit_missing_title_and_slug_is_dropped(self, tmp_path):
        extractor = _make_extractor(tmp_path)
        response = json.dumps({
            "units": [
                {"slug": "ok", "title": "ok"},
                {"unit_type": "fact", "content": "no title or slug"},
                {"slug": "second", "title": "Second"},
            ],
            "skip_reason": "",
        })
        concepts = extractor._parse_v2_response(response, Path("/fake/x.md"))
        slugs = {c["concept_name"] for c in concepts}
        assert slugs == {"ok", "second"}

    def test_v2_unit_type_passes_through_to_entity_type(self, tmp_path):
        # BL-025/026: each of the 10 v2 unit kinds is also a valid
        # entity_type — pre-fix behaviour collapsed everything except
        # method/procedure to KIND_CONCEPT, defeating Reader-side
        # type filtering on the 89% of evergreens that aren't
        # methods.  Now ``unit_type`` passes through unchanged.
        for ut in ("fact", "method", "procedure", "tradeoff",
                   "failure_mode", "case_detail", "learning",
                   "decision", "quote", "counterexample"):
            unit = {"slug": f"x-{ut}", "title": "X", "unit_type": ut}
            converted = EvergreenExtractor._unit_to_concept(unit)
            assert converted["entity_type"] == ut, (
                f"unit_type={ut} should pass through to entity_type"
            )

    def test_unknown_unit_type_falls_back_to_concept(self, tmp_path):
        # Defence in depth: an LLM that emits an unrecognised
        # ``unit_type`` (drift, schema bug) gets KIND_CONCEPT so
        # downstream code never sees an arbitrary string.
        unit = {"slug": "x-junk", "title": "X", "unit_type": "made-up-type"}
        converted = EvergreenExtractor._unit_to_concept(unit)
        assert converted["entity_type"] == "concept"


# ---------------------------------------------------------------------------
# create_evergreen_note v2 body template
# ---------------------------------------------------------------------------


class TestEvergreenNoteV2Template:
    def _source_file(self, tmp_path: Path) -> Path:
        """A minimal processed-source markdown that
        ``_read_source_provenance`` can parse."""
        path = tmp_path / "2026-04-09_synthetic.md"
        path.write_text(
            "---\n"
            "title: \"Synthetic source\"\n"
            "source: https://example.com/article\n"
            "date: 2026-04-09\n"
            "type: article\n"
            "---\n\n"
            "# body\n",
            encoding="utf-8",
        )
        return path

    def test_no_legacy_template_sections(self, tmp_path):
        """v1 forced ``定义 / 详细解释 / 为什么重要`` headers — v2 body
        is whatever ``content`` contained.  Regression guard."""
        extractor = _make_extractor(tmp_path)
        source = self._source_file(tmp_path)
        concept = {
            "concept_name": "x",
            "title": "X exists",
            "explanation": "specific body content",
            "unit_type": "fact",
            "epistemic_role": "fact",
            "source_anchor": "from source",
            "specifics": ["numbers"],
            "related_concepts": [],
            "entity_type": "concept",
        }
        note = extractor.create_evergreen_note(concept, source)
        # No forced section headings
        assert "一句话定义" not in note
        assert "## 📝 详细解释" not in note
        assert "### 是什么？" not in note
        assert "### 为什么重要？" not in note
        # New v2 markers ARE present
        assert "extraction_prompt_version: v2" in note
        assert "unit_type: fact" in note
        assert "epistemic_role: fact" in note
        assert 'source_anchor: "from source"' in note or 'source_anchor: from source' in note
        assert "specifics: [numbers]" in note

    def test_related_section_omitted_when_empty(self, tmp_path):
        """Empty ``related_concepts`` → no ``## Related`` heading.
        Avoids dangling section headers in the rendered note."""
        extractor = _make_extractor(tmp_path)
        source = self._source_file(tmp_path)
        concept = {
            "concept_name": "x", "title": "X", "explanation": "body",
            "unit_type": "fact", "epistemic_role": "fact",
            "source_anchor": "anchor", "specifics": [],
            "related_concepts": [],
            "entity_type": "concept",
        }
        note = extractor.create_evergreen_note(concept, source)
        assert "## Related" not in note

    def test_related_section_present_when_non_empty(self, tmp_path):
        extractor = _make_extractor(tmp_path)
        source = self._source_file(tmp_path)
        concept = {
            "concept_name": "x", "title": "X", "explanation": "body",
            "unit_type": "fact", "epistemic_role": "fact",
            "source_anchor": "anchor", "specifics": [],
            "related_concepts": ["foo-bar", "baz-qux"],
            "entity_type": "concept",
        }
        note = extractor.create_evergreen_note(concept, source)
        assert "## Related" in note
        assert "[[foo-bar]]" in note
        assert "[[baz-qux]]" in note

    def test_source_anchor_block_renders(self, tmp_path):
        extractor = _make_extractor(tmp_path)
        source = self._source_file(tmp_path)
        concept = {
            "concept_name": "x", "title": "X", "explanation": "body",
            "unit_type": "fact", "epistemic_role": "fact",
            "source_anchor": "exact phrase from source",
            "specifics": ["names"],
            "related_concepts": [],
            "entity_type": "concept",
        }
        note = extractor.create_evergreen_note(concept, source)
        # The blockquote shows the verbatim anchor for human review
        assert '> **Source anchor**: "exact phrase from source"' in note

    def test_source_anchor_block_omitted_when_empty(self, tmp_path):
        """No anchor → no anchor block.  (v2 prompt requires anchor,
        but the renderer must tolerate missing values gracefully so a
        partial parse doesn't render a malformed block.)"""
        extractor = _make_extractor(tmp_path)
        source = self._source_file(tmp_path)
        concept = {
            "concept_name": "x", "title": "X", "explanation": "body",
            "unit_type": "fact", "epistemic_role": "fact",
            "source_anchor": "",
            "specifics": [],
            "related_concepts": [],
            "entity_type": "concept",
        }
        note = extractor.create_evergreen_note(concept, source)
        assert "Source anchor" not in note


# ---------------------------------------------------------------------------
# Legacy tagging command
# ---------------------------------------------------------------------------


class TestLegacyTagging:
    def _legacy_evergreen(self, tmp_path: Path, name: str = "legacy.md") -> Path:
        """An evergreen written by the v1 absorb path — has no
        ``extraction_prompt_version`` field."""
        path = tmp_path / name
        path.write_text(
            "---\n"
            "note_id: legacy\n"
            "title: \"Legacy note\"\n"
            "type: evergreen\n"
            "entity_type: concept\n"
            "date: 2026-04-09\n"
            "tags: [evergreen]\n"
            "aliases: [\"legacy\"]\n"
            "---\n\n"
            "# Legacy note\n\n"
            "> **一句话定义**: ...\n",
            encoding="utf-8",
        )
        return path

    def test_classify_dry_run_does_not_mutate(self, tmp_path):
        path = self._legacy_evergreen(tmp_path)
        original = path.read_text(encoding="utf-8")
        result = _classify_and_tag(path, tagged_at="2026-05-15T00:00:00+00:00", write=False)
        assert result.action == "tagged"
        assert path.read_text(encoding="utf-8") == original

    def test_classify_write_adds_three_markers(self, tmp_path):
        path = self._legacy_evergreen(tmp_path)
        result = _classify_and_tag(path, tagged_at="2026-05-15T00:00:00+00:00", write=True)
        assert result.action == "tagged"
        new_text = path.read_text(encoding="utf-8")
        assert "extraction_prompt_version: v1" in new_text
        assert "legacy_unverified: true" in new_text
        assert 'legacy_tagged_at: "2026-05-15T00:00:00+00:00"' in new_text
        # Body untouched
        assert "# Legacy note" in new_text
        assert "> **一句话定义**: ..." in new_text

    def test_classify_idempotent_second_run_skips(self, tmp_path):
        path = self._legacy_evergreen(tmp_path)
        _classify_and_tag(path, tagged_at="2026-05-15T00:00:00+00:00", write=True)
        # Second run
        result = _classify_and_tag(path, tagged_at="2026-05-15T00:00:00+00:00", write=True)
        assert result.action == "skipped_already_tagged"

    def test_classify_skips_v2_files(self, tmp_path):
        v2_path = tmp_path / "v2.md"
        v2_path.write_text(
            "---\n"
            "note_id: v2-note\n"
            "title: \"V2 note\"\n"
            "extraction_prompt_version: v2\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )
        result = _classify_and_tag(v2_path, tagged_at="2026-05-15T00:00:00+00:00", write=True)
        assert result.action == "skipped_v2"

    def test_untag_removes_three_markers(self, tmp_path):
        path = self._legacy_evergreen(tmp_path)
        _classify_and_tag(path, tagged_at="2026-05-15T00:00:00+00:00", write=True)
        result = _untag_file(path, write=True)
        assert result.action == "untagged"
        new_text = path.read_text(encoding="utf-8")
        assert "legacy_unverified" not in new_text
        assert "legacy_tagged_at" not in new_text
        assert "extraction_prompt_version" not in new_text

    def test_skips_files_without_frontmatter(self, tmp_path):
        path = tmp_path / "no-fm.md"
        path.write_text("# just a heading\n\nbody\n", encoding="utf-8")
        result = _classify_and_tag(path, tagged_at="2026-05-15T00:00:00+00:00", write=True)
        assert result.action == "skipped_no_frontmatter"

    def test_main_writes_manifest(self, tmp_path, capsys):
        # Set up a vault skeleton
        vault = tmp_path
        for d in ["10-Knowledge/Evergreen", "20-Areas", "50-Inbox", "60-Logs", "70-Archive"]:
            (vault / d).mkdir(parents=True, exist_ok=True)
        (vault / ".obsidian").mkdir(exist_ok=True)
        legacy = vault / "10-Knowledge" / "Evergreen" / "x.md"
        legacy.write_text(
            "---\nnote_id: x\ntitle: X\ntype: evergreen\n---\n\nbody\n",
            encoding="utf-8",
        )

        rc = tag_legacy_main([
            "--vault-dir", str(vault),
            "--write",
            "--run-id", "test-run",
        ])
        assert rc == 0
        manifest_path = vault / "60-Logs" / "legacy-tag" / "test-run" / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["mode"] == "tag"
        assert manifest["write"] is True
        assert manifest["total_files"] == 1
        assert manifest["by_action"].get("tagged") == 1
        # And the legacy file actually got tagged
        assert "legacy_unverified: true" in legacy.read_text(encoding="utf-8")
