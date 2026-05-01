from __future__ import annotations

from datetime import date

from ovp_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_projection_vault(temp_vault):
    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    beta = temp_vault / "10-Knowledge" / "Evergreen" / "Beta.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-30
---

# Alpha

Alpha is a compiled knowledge object.

Links to [[beta]].
""",
        encoding="utf-8",
    )
    beta.write_text(
        """---
note_id: beta
title: Beta
type: evergreen
date: 2026-04-30
---

# Beta

Beta extends Alpha.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)


def _assert_projection_label(
    payload: dict,
    *,
    surface: str,
    projection_kind: str = "access_surface",
) -> None:
    label = payload["projection_label"]
    assert label["projection_schema_version"] == 1
    assert label["projection_kind"] == projection_kind
    assert label["projection_surface"] == surface
    assert label["projection_layer"] == "Layer 3"
    assert label["projection_authority_boundary"] == "derived_not_authority"
    assert label["projection_derived_from"]
    assert label["projection_rebuild_policy"]


def test_core_reader_payloads_carry_projection_labels(temp_vault):
    from ovp_pipeline.ui.view_models import (
        build_briefing_payload,
        build_cluster_browser_payload,
        build_graph_map_payload,
        build_object_page_payload,
        build_runtime_home_payload,
        build_search_payload,
        build_truth_dashboard_payload,
    )

    _seed_projection_vault(temp_vault)

    cases = [
        (build_runtime_home_payload(temp_vault), "reader_home"),
        (build_truth_dashboard_payload(temp_vault), "ops_dashboard"),
        (build_search_payload(temp_vault, query="alpha"), "search_results"),
        (build_object_page_payload(temp_vault, "alpha"), "object_page"),
        (build_briefing_payload(temp_vault), "briefing"),
        (build_graph_map_payload(temp_vault), "graph_map"),
        (build_cluster_browser_payload(temp_vault), "graph_clusters"),
    ]
    for payload, surface in cases:
        _assert_projection_label(payload, surface=surface)


def test_projection_label_helper_keeps_stable_boundary_fields():
    from ovp_pipeline.projection_labels import projection_label

    label = projection_label(
        surface="reader_home",
        projection_kind="access_surface",
        layer="Layer 3",
        owner_pack="research-tech",
        generated_by="build_runtime_home_payload",
        derived_from=("knowledge.db", "runtime ledgers"),
        rebuild_policy="read_time",
    )

    assert label == {
        "projection_schema_version": 1,
        "projection_kind": "access_surface",
        "projection_surface": "reader_home",
        "projection_layer": "Layer 3",
        "projection_owner_pack": "research-tech",
        "projection_generated_by": "build_runtime_home_payload",
        "projection_derived_from": ["knowledge.db", "runtime ledgers"],
        "projection_rebuild_policy": "read_time",
        "projection_authority_boundary": "derived_not_authority",
    }


def test_markdown_projection_lines_are_stable_metadata():
    from ovp_pipeline.projection_labels import (
        frontmatter_projection_fields,
        markdown_projection_lines,
    )

    assert markdown_projection_lines(
        surface="cluster_view",
        projection_kind="compiled_wiki_projection",
        owner_pack="research-tech",
        generated_by="cluster_view",
        derived_from=("knowledge.db",),
        rebuild_policy="on_derived_refresh",
    ) == [
        "- projection_schema_version: 1",
        "- projection_kind: compiled_wiki_projection",
        "- projection_surface: cluster_view",
        "- projection_layer: Layer 3",
        "- projection_owner_pack: research-tech",
        "- projection_generated_by: cluster_view",
        "- projection_derived_from: knowledge.db",
        "- projection_rebuild_policy: on_derived_refresh",
        "- projection_authority_boundary: derived_not_authority",
    ]
    assert frontmatter_projection_fields(
        surface="cluster_view",
        projection_kind="compiled_wiki_projection",
        owner_pack="research-tech",
        generated_by="cluster_view",
        derived_from=("knowledge.db",),
        rebuild_policy="on_derived_refresh",
    ) == [
        "projection_schema_version: 1",
        "projection_kind: compiled_wiki_projection",
        "projection_surface: cluster_view",
        "projection_layer: Layer 3",
        "projection_owner_pack: research-tech",
        "projection_generated_by: cluster_view",
        "projection_derived_from: [knowledge.db]",
        "projection_rebuild_policy: on_derived_refresh",
        "projection_authority_boundary: derived_not_authority",
    ]


def test_materialized_projection_artifacts_carry_labels(temp_vault):
    from ovp_pipeline.commands.working_memory import build_working_memory
    from ovp_pipeline.materializers.crystal import materialize_crystal
    from ovp_pipeline.materializers.object_page import materialize_object_page
    from ovp_pipeline.materializers.topic_view import materialize_topic_view

    _seed_projection_vault(temp_vault)

    working_memory = build_working_memory(temp_vault, target_date=date(2026, 4, 30))
    working_memory_text = working_memory.read_text(encoding="utf-8")
    assert "projection_kind: context_pack_projection" in working_memory_text
    assert "projection_surface: working_memory" in working_memory_text
    assert "projection_authority_boundary: derived_not_authority" in working_memory_text

    crystal = materialize_crystal(
        {"generated_at": "2026-04-30T00:00:00Z", "active_topics": []},
        temp_vault,
        when=date(2026, 4, 30),
    )
    crystal_text = crystal.path.read_text(encoding="utf-8")
    assert "projection_kind: context_pack_projection" in crystal_text
    assert "projection_surface: crystal" in crystal_text
    assert "projection_authority_boundary: derived_not_authority" in crystal_text

    object_page = materialize_object_page(temp_vault, pack_name="research-tech", object_id="alpha")
    object_page_text = object_page.read_text(encoding="utf-8")
    assert "- projection_kind: compiled_wiki_projection" in object_page_text
    assert "- projection_surface: object_page" in object_page_text

    topic_view = materialize_topic_view(temp_vault, pack_name="research-tech", view_name="topic/overview")
    topic_view_text = topic_view.read_text(encoding="utf-8")
    assert "- projection_kind: compiled_wiki_projection" in topic_view_text
    assert "- projection_surface: topic_view" in topic_view_text


def test_query_moc_carries_projection_frontmatter(temp_vault):
    from ovp_pipeline.query_tool import VaultQuerier

    target_file = temp_vault / "20-Areas" / "Queries" / "Alpha.md"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("# Alpha\n", encoding="utf-8")

    querier = VaultQuerier(temp_vault, pack="research-tech")
    querier._update_moc_queries("What is Alpha?", target_file, {})

    moc_text = (temp_vault / "10-Knowledge" / "Atlas" / "MOC-Queries.md").read_text(
        encoding="utf-8"
    )
    assert "projection_kind: compiled_wiki_projection" in moc_text
    assert "projection_surface: moc_queries" in moc_text
    assert "projection_authority_boundary: derived_not_authority" in moc_text
    assert "[[20-Areas/Queries/Alpha|What is Alpha?]]" in moc_text
