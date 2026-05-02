"""Tests for Phase B: Entity kind filtering and statistics APIs."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_multi_kind_vault(temp_vault: Path) -> Path:
    """Create a vault with notes of different entity_type values."""
    eg_dir = temp_vault / "10-Knowledge" / "Evergreen"
    eg_dir.mkdir(parents=True, exist_ok=True)

    notes = [
        ("rag", "RAG", "concept", "Retrieval-Augmented Generation."),
        ("attention", "Attention Mechanism", "concept", "Core transformer technique."),
        ("langchain", "LangChain", "tool", "Framework for LLM apps."),
        ("openai", "OpenAI", "company", "AI research company."),
        ("ilya-sutskever", "Ilya Sutskever", "person", "Co-founder of OpenAI."),
        ("react-pattern", "ReAct", "framework", "Reasoning + Acting."),
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
    return temp_vault


def test_list_objects_no_kind_filter_returns_all(temp_vault):
    from ovp_pipeline.truth_api import list_objects

    vault = _seed_multi_kind_vault(temp_vault)
    objects = list_objects(vault)
    assert len(objects) == 6


def test_list_objects_filter_by_concept(temp_vault):
    from ovp_pipeline.truth_api import list_objects

    vault = _seed_multi_kind_vault(temp_vault)
    objects = list_objects(vault, object_kind="concept")
    assert len(objects) == 2
    assert all(o["object_kind"] == "concept" for o in objects)


def test_list_objects_filter_by_tool(temp_vault):
    from ovp_pipeline.truth_api import list_objects

    vault = _seed_multi_kind_vault(temp_vault)
    objects = list_objects(vault, object_kind="tool")
    assert len(objects) == 1
    assert objects[0]["object_id"] == "langchain"


def test_list_objects_filter_by_person(temp_vault):
    from ovp_pipeline.truth_api import list_objects

    vault = _seed_multi_kind_vault(temp_vault)
    objects = list_objects(vault, object_kind="person")
    assert len(objects) == 1
    assert objects[0]["title"] == "Ilya Sutskever"


def test_list_objects_filter_by_nonexistent_kind_returns_empty(temp_vault):
    from ovp_pipeline.truth_api import list_objects

    vault = _seed_multi_kind_vault(temp_vault)
    objects = list_objects(vault, object_kind="event")
    assert len(objects) == 0


def test_list_objects_kind_filter_with_query(temp_vault):
    from ovp_pipeline.truth_api import list_objects

    vault = _seed_multi_kind_vault(temp_vault)
    objects = list_objects(vault, object_kind="concept", query="rag")
    assert len(objects) == 1
    assert objects[0]["object_id"] == "rag"


def test_count_objects_with_kind_filter(temp_vault):
    from ovp_pipeline.truth_api import count_objects

    vault = _seed_multi_kind_vault(temp_vault)
    assert count_objects(vault) == 6
    assert count_objects(vault, object_kind="concept") == 2
    assert count_objects(vault, object_kind="company") == 1
    assert count_objects(vault, object_kind="event") == 0


def test_list_object_kind_stats(temp_vault):
    from ovp_pipeline.truth_api import list_object_kind_stats

    vault = _seed_multi_kind_vault(temp_vault)
    stats = list_object_kind_stats(vault)
    kind_map = {s["object_kind"]: s["count"] for s in stats}
    assert kind_map["concept"] == 2
    assert kind_map["tool"] == 1
    assert kind_map["company"] == 1
    assert kind_map["person"] == 1
    assert kind_map["framework"] == 1
    assert all("label" in s for s in stats)


def test_list_object_kind_stats_labels(temp_vault):
    from ovp_pipeline.truth_api import list_object_kind_stats

    vault = _seed_multi_kind_vault(temp_vault)
    stats = list_object_kind_stats(vault)
    label_map = {s["object_kind"]: s["label"] for s in stats}
    assert label_map["concept"] == "Concept"
    assert label_map["person"] == "Person"
    assert label_map["tool"] == "Tool"


def test_build_objects_index_payload_includes_kind_stats(temp_vault):
    from ovp_pipeline.ui.view_models import build_objects_index_payload

    vault = _seed_multi_kind_vault(temp_vault)
    payload = build_objects_index_payload(vault)
    assert "kind_stats" in payload
    assert len(payload["kind_stats"]) > 0
    assert "object_kind" in payload
    assert payload["object_kind"] == ""


def test_build_objects_index_payload_filters_by_kind(temp_vault):
    from ovp_pipeline.ui.view_models import build_objects_index_payload

    vault = _seed_multi_kind_vault(temp_vault)
    payload = build_objects_index_payload(vault, object_kind="tool")
    assert payload["count"] == 1
    assert payload["total_count"] == 1
    assert payload["items"][0]["object_kind"] == "tool"
    assert payload["object_kind"] == "tool"


def test_kind_filter_normalizes_input(temp_vault):
    from ovp_pipeline.truth_api import list_objects

    vault = _seed_multi_kind_vault(temp_vault)
    objects = list_objects(vault, object_kind="CONCEPT")
    assert len(objects) == 2
    objects2 = list_objects(vault, object_kind=" concept ")
    assert len(objects2) == 2
