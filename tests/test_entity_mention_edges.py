"""Tests for Phase C: Entity mention edges — kind-enriched backlinks and mention statistics."""
from __future__ import annotations

from pathlib import Path

import pytest

from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_mention_vault(temp_vault: Path) -> Path:
    """Create a vault where typed entities mention (link to) each other via wikilinks."""
    eg_dir = temp_vault / "10-Knowledge" / "Evergreen"
    eg_dir.mkdir(parents=True, exist_ok=True)

    areas_dir = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04"
    areas_dir.mkdir(parents=True, exist_ok=True)

    eg_dir_notes = [
        ("rag", "RAG", "concept", "Retrieval-Augmented Generation combines [[attention]] with retrieval."),
        ("attention", "Attention Mechanism", "concept", "Core transformer technique used by [[openai]]."),
        ("langchain", "LangChain", "tool", "Framework using [[rag]] and [[attention]] patterns."),
        ("openai", "OpenAI", "company", "AI research company that built [[attention]]-based models."),
        ("ilya-sutskever", "Ilya Sutskever", "person", "Co-founder of [[openai]], researched [[attention]]."),
    ]
    for slug, title, kind, body in eg_dir_notes:
        (eg_dir / f"{slug}.md").write_text(
            f"""---
note_id: {slug}
title: "{title}"
type: evergreen
entity_type: {kind}
date: 2026-04-30
---

# {title}

{body}
""",
            encoding="utf-8",
        )

    (areas_dir / "rag-overview_深度解读.md").write_text(
        """---
note_id: rag-overview
title: "RAG Architecture Overview"
type: interpretation
date: 2026-04-30
---

# RAG Architecture Overview

This article explores [[rag]] and [[attention]] in depth.
It also mentions [[openai]] as the key company.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    return temp_vault


class TestMentionKindEnrichment:
    """Verify that mention/backlink queries return object_kind for the source page."""

    def test_get_object_detail_mentions_include_object_kind(self, temp_vault):
        from ovp_pipeline.truth_api import get_object_detail

        vault = _seed_mention_vault(temp_vault)
        detail = get_object_detail(vault, "attention")
        source_notes = detail["provenance"]["source_notes"]
        for note in source_notes:
            assert "object_kind" in note, f"Missing object_kind on mention: {note}"

    def test_get_object_detail_mention_kind_values(self, temp_vault):
        from ovp_pipeline.truth_api import get_object_detail

        vault = _seed_mention_vault(temp_vault)
        detail = get_object_detail(vault, "attention")
        source_notes = detail["provenance"]["source_notes"]
        source_kinds = {n["slug"]: n["object_kind"] for n in source_notes}
        if "langchain" in source_kinds:
            assert source_kinds["langchain"] == "tool"
        if "openai" in source_kinds:
            assert source_kinds["openai"] == "company"
        if "ilya-sutskever" in source_kinds:
            assert source_kinds["ilya-sutskever"] == "person"

    def test_get_object_detail_interpretation_mention_has_empty_kind(self, temp_vault):
        """Interpretations are not objects, so object_kind should be empty string."""
        from ovp_pipeline.truth_api import get_object_detail

        vault = _seed_mention_vault(temp_vault)
        detail = get_object_detail(vault, "rag")
        source_notes = detail["provenance"]["source_notes"]
        interps = [n for n in source_notes if n["note_type"] == "interpretation"]
        for interp in interps:
            assert interp["object_kind"] == "", f"Interpretation should have empty object_kind: {interp}"

    def test_get_object_provenance_map_includes_object_kind(self, temp_vault):
        from ovp_pipeline.truth_api import get_object_provenance_map

        vault = _seed_mention_vault(temp_vault)
        prov = get_object_provenance_map(vault, ["attention", "rag"])
        for object_id, data in prov.items():
            for note in data["source_notes"]:
                assert "object_kind" in note, f"Missing object_kind in provenance for {object_id}: {note}"


class TestMentionKindStats:
    """Verify list_mention_kind_stats returns per-kind counts of pages mentioning an object."""

    def test_mention_kind_stats_returns_list(self, temp_vault):
        from ovp_pipeline.truth_api import list_mention_kind_stats

        vault = _seed_mention_vault(temp_vault)
        stats = list_mention_kind_stats(vault, "attention")
        assert isinstance(stats, list)
        assert len(stats) > 0

    def test_mention_kind_stats_structure(self, temp_vault):
        from ovp_pipeline.truth_api import list_mention_kind_stats

        vault = _seed_mention_vault(temp_vault)
        stats = list_mention_kind_stats(vault, "attention")
        for item in stats:
            assert "object_kind" in item
            assert "label" in item
            assert "count" in item
            assert isinstance(item["count"], int)
            assert item["count"] > 0

    def test_mention_kind_stats_for_attention(self, temp_vault):
        """attention is mentioned by: rag(concept), langchain(tool), openai(company),
        ilya-sutskever(person), and rag-overview(interpretation, not an object -> kind='')."""
        from ovp_pipeline.truth_api import list_mention_kind_stats

        vault = _seed_mention_vault(temp_vault)
        stats = list_mention_kind_stats(vault, "attention")
        kind_map = {s["object_kind"]: s["count"] for s in stats}
        total = sum(s["count"] for s in stats)
        assert total >= 3, f"Expected at least 3 mentions of 'attention', got {total}: {kind_map}"

    def test_mention_kind_stats_empty_for_unmentioned_object(self, temp_vault):
        from ovp_pipeline.truth_api import list_mention_kind_stats

        vault = _seed_mention_vault(temp_vault)
        stats = list_mention_kind_stats(vault, "nonexistent-object")
        assert stats == []

    def test_mention_kind_stats_note_label(self, temp_vault):
        """Untyped pages (interpretations) should get label 'note'."""
        from ovp_pipeline.truth_api import list_mention_kind_stats

        vault = _seed_mention_vault(temp_vault)
        stats = list_mention_kind_stats(vault, "rag")
        labels = {s["object_kind"]: s["label"] for s in stats}
        if "" in labels:
            assert labels[""] == "note"


class TestObjectPagePayloadMentionStats:
    """Verify build_object_page_payload includes mention_kind_stats."""

    def test_payload_contains_mention_kind_stats(self, temp_vault):
        from ovp_pipeline.ui.view_models import build_object_page_payload

        vault = _seed_mention_vault(temp_vault)
        payload = build_object_page_payload(vault, "attention")
        assert "mention_kind_stats" in payload
        assert isinstance(payload["mention_kind_stats"], list)

    def test_payload_mention_kind_stats_matches_api(self, temp_vault):
        from ovp_pipeline.truth_api import list_mention_kind_stats
        from ovp_pipeline.ui.view_models import build_object_page_payload

        vault = _seed_mention_vault(temp_vault)
        payload = build_object_page_payload(vault, "attention")
        direct_stats = list_mention_kind_stats(vault, "attention")
        assert payload["mention_kind_stats"] == direct_stats
