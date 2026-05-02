"""Tests for Phase D: Typed relations — kind-enriched relation/edge queries."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_relation_vault(temp_vault: Path) -> Path:
    """Create a vault with typed entities and manually insert semantic relations."""
    eg_dir = temp_vault / "10-Knowledge" / "Evergreen"
    eg_dir.mkdir(parents=True, exist_ok=True)

    notes = [
        ("transformer", "Transformer", "concept", "The core architecture."),
        ("attention", "Attention Mechanism", "concept", "Key technique in [[transformer]]."),
        ("pytorch", "PyTorch", "tool", "Framework implementing [[transformer]]."),
        ("openai", "OpenAI", "company", "Built models using [[transformer]]."),
        ("ilya-sutskever", "Ilya Sutskever", "person", "Researched [[attention]]."),
    ]
    for slug, title, kind, body in notes:
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

    rebuild_knowledge_index(temp_vault)

    db_path = temp_vault / "60-Logs" / "knowledge.db"
    with sqlite3.connect(db_path) as conn:
        pack = "research-tech"
        relations = [
            (pack, "transformer", "attention", "uses", "transformer"),
            (pack, "transformer", "pytorch", "implemented_by", "transformer"),
            (pack, "openai", "transformer", "uses", "openai"),
            (pack, "ilya-sutskever", "attention", "researches", "ilya-sutskever"),
        ]
        for p, src, tgt, rel_type, ev_slug in relations:
            conn.execute(
                """INSERT INTO relations
                   (pack, source_object_id, target_object_id, relation_type,
                    evidence_source_slug, status)
                   VALUES (?, ?, ?, ?, ?, 'unverified')""",
                (p, src, tgt, rel_type, ev_slug),
            )
        edges = [
            (pack, "e1", "transformer", "attention", "uses", 1.0, "transformer"),
            (pack, "e2", "transformer", "pytorch", "implemented_by", 0.8, "transformer"),
            (pack, "e3", "openai", "transformer", "uses", 0.9, "openai"),
            (pack, "e4", "ilya-sutskever", "attention", "researches", 0.7, "ilya-sutskever"),
        ]
        for p, eid, src, tgt, ek, w, ev in edges:
            conn.execute(
                """INSERT OR REPLACE INTO graph_edges
                   (pack, edge_id, source_object_id, target_object_id, edge_kind,
                    weight, evidence_source_slug)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (p, eid, src, tgt, ek, w, ev),
            )
        conn.commit()

    return temp_vault


class TestRelationKindEnrichment:
    """Verify that relation queries include source_kind and target_kind."""

    def test_get_object_detail_relations_have_source_kind(self, temp_vault):
        from ovp_pipeline.truth_api import get_object_detail

        vault = _seed_relation_vault(temp_vault)
        detail = get_object_detail(vault, "transformer")
        for rel in detail["relations"]:
            assert "source_kind" in rel
            assert "target_kind" in rel

    def test_get_object_detail_relation_kind_values(self, temp_vault):
        from ovp_pipeline.truth_api import get_object_detail

        vault = _seed_relation_vault(temp_vault)
        detail = get_object_detail(vault, "transformer")
        rels = detail["relations"]
        by_target = {r["target_object_id"]: r for r in rels}

        assert by_target["attention"]["source_kind"] == "concept"
        assert by_target["attention"]["target_kind"] == "concept"
        assert by_target["pytorch"]["target_kind"] == "tool"

    def test_graph_edges_have_source_and_target_kind(self, temp_vault):
        from ovp_pipeline.truth_api import list_graph_edges_for_object_scope

        vault = _seed_relation_vault(temp_vault)
        edges = list_graph_edges_for_object_scope(
            vault, object_ids=["transformer", "attention", "pytorch"]
        )
        assert len(edges) >= 1
        for edge in edges:
            assert "source_kind" in edge
            assert "target_kind" in edge

    def test_graph_edges_kind_values(self, temp_vault):
        from ovp_pipeline.truth_api import list_graph_edges_for_object_scope

        vault = _seed_relation_vault(temp_vault)
        edges = list_graph_edges_for_object_scope(
            vault, object_ids=["transformer", "attention", "pytorch"]
        )
        by_id = {e["edge_id"]: e for e in edges}

        e1 = by_id.get("e1")
        assert e1 is not None
        assert e1["source_kind"] == "concept"
        assert e1["target_kind"] == "concept"

        e2 = by_id.get("e2")
        assert e2 is not None
        assert e2["source_kind"] == "concept"
        assert e2["target_kind"] == "tool"

    def test_topic_neighborhood_edges_have_kind(self, temp_vault):
        from ovp_pipeline.truth_api import get_topic_neighborhood

        vault = _seed_relation_vault(temp_vault)
        nb = get_topic_neighborhood(vault, "transformer")
        for edge in nb["edges"]:
            assert "source_kind" in edge
            assert "target_kind" in edge
            assert edge["source_kind"] == "concept"

    def test_topic_neighborhood_edge_target_kinds(self, temp_vault):
        from ovp_pipeline.truth_api import get_topic_neighborhood

        vault = _seed_relation_vault(temp_vault)
        nb = get_topic_neighborhood(vault, "transformer")
        target_kinds = {e["target_object_id"]: e["target_kind"] for e in nb["edges"]}
        assert target_kinds.get("attention") == "concept"
        assert target_kinds.get("pytorch") == "tool"


class TestRelationKindStats:
    """Verify list_relation_kind_stats returns per-kind aggregates."""

    def test_returns_list(self, temp_vault):
        from ovp_pipeline.truth_api import list_relation_kind_stats

        vault = _seed_relation_vault(temp_vault)
        stats = list_relation_kind_stats(vault, "transformer")
        assert isinstance(stats, list)
        assert len(stats) >= 1

    def test_structure(self, temp_vault):
        from ovp_pipeline.truth_api import list_relation_kind_stats

        vault = _seed_relation_vault(temp_vault)
        stats = list_relation_kind_stats(vault, "transformer")
        for item in stats:
            assert "object_kind" in item
            assert "label" in item
            assert "count" in item
            assert isinstance(item["count"], int)

    def test_transformer_targets(self, temp_vault):
        from ovp_pipeline.truth_api import list_relation_kind_stats

        vault = _seed_relation_vault(temp_vault)
        stats = list_relation_kind_stats(vault, "transformer")
        by_kind = {s["object_kind"]: s["count"] for s in stats}
        assert by_kind.get("concept") == 1
        assert by_kind.get("tool") == 1

    def test_empty_for_no_relations(self, temp_vault):
        from ovp_pipeline.truth_api import list_relation_kind_stats

        vault = _seed_relation_vault(temp_vault)
        stats = list_relation_kind_stats(vault, "nonexistent-object-id")
        assert stats == []

    def test_payload_contains_relation_kind_stats(self, temp_vault):
        from ovp_pipeline.ui.view_models import build_object_page_payload

        vault = _seed_relation_vault(temp_vault)
        payload = build_object_page_payload(vault, "transformer")
        assert "relation_kind_stats" in payload
        assert isinstance(payload["relation_kind_stats"], list)

    def test_payload_relation_kind_stats_matches_api(self, temp_vault):
        from ovp_pipeline.truth_api import list_relation_kind_stats
        from ovp_pipeline.ui.view_models import build_object_page_payload

        vault = _seed_relation_vault(temp_vault)
        payload = build_object_page_payload(vault, "transformer")
        api_stats = list_relation_kind_stats(vault, "transformer")
        assert payload["relation_kind_stats"] == api_stats
