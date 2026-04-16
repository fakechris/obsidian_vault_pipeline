from __future__ import annotations

import sqlite3

from openclaw_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_truth_store(temp_vault):
    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    beta = temp_vault / "10-Knowledge" / "Evergreen" / "Beta.md"
    conflict = temp_vault / "10-Knowledge" / "Evergreen" / "Conflict.md"

    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.

Links to [[beta]].
""",
        encoding="utf-8",
    )
    beta.write_text(
        """---
note_id: beta
title: Beta
type: evergreen
date: 2026-04-13
---

# Beta

Beta extends Alpha.
""",
        encoding="utf-8",
    )
    conflict.write_text(
        """---
note_id: conflict
title: Conflict
type: evergreen
date: 2026-04-13
---

# Conflict

Alpha does not support local-first execution.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)


def _resolve_all_contradictions(temp_vault):
    from openclaw_pipeline.runtime import VaultLayout

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE contradictions
            SET status = 'resolved',
                resolution_note = 'reviewed',
                resolved_at = '2026-04-14T00:00:00Z'
            """
        )
        conn.commit()


def test_build_object_page_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_object_page_payload

    _seed_truth_store(temp_vault)

    payload = build_object_page_payload(temp_vault, "alpha")

    assert payload["screen"] == "object/page"
    assert payload["object"]["object_id"] == "alpha"
    assert payload["summary"]["summary_text"] == "Alpha supports local-first execution."
    assert payload["claim_count"] == 1
    assert payload["relation_count"] == 1
    assert payload["contradiction_count"] == 1
    assert payload["links"]["topic_path"] == "/topic?id=alpha"
    assert payload["links"]["events_path"] == "/events?q=alpha"
    assert payload["links"]["contradictions_path"] == "/contradictions?q=alpha"
    assert payload["links"]["summaries_path"] == "/summaries?q=alpha"
    assert payload["context"]["source_slug"] == "alpha"
    assert payload["section_nav"][0]["href"] == "#summary"
    assert payload["review_context"]["object_count"] == 1
    assert payload["review_context"]["open_contradiction_count"] == 1
    assert payload["review_context"]["latest_event_date"] == "2026-04-13"
    assert payload["review_history"] == []
    assert payload["stale_summary_details"] == []


def test_build_object_page_payload_includes_provenance(temp_vault):
    from openclaw_pipeline.ui.view_models import build_object_page_payload

    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Source Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: source-deep-dive
title: Source Deep Dive
type: deep_dive
date: 2026-04-13
---

# Source Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_object_page_payload(temp_vault, "alpha")

    assert payload["provenance"]["evergreen_path"].endswith("10-Knowledge/Evergreen/Alpha.md")
    assert payload["provenance"]["source_notes"][0]["slug"] == "source-deep-dive"
    assert payload["provenance"]["mocs"][0]["slug"] == "atlas-index"


def test_build_topic_overview_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_topic_overview_payload

    _seed_truth_store(temp_vault)

    payload = build_topic_overview_payload(temp_vault, "alpha")

    assert payload["screen"] == "overview/topic"
    assert payload["center"]["object_id"] == "alpha"
    assert [item["object_id"] for item in payload["neighbors"]] == ["beta"]
    assert payload["edge_count"] == 1
    assert payload["links"]["center_object_path"] == "/object?id=alpha"
    assert payload["links"]["events_path"] == "/events?q=alpha"
    assert payload["center_summary"] == "Alpha supports local-first execution."
    assert payload["links"]["summaries_path"] == "/summaries?q=alpha"
    assert payload["review_context"]["object_count"] == 2
    assert payload["review_context"]["open_contradiction_count"] == 1
    assert payload["review_history"] == []
    assert payload["scoped_open_contradiction_ids"]
    assert payload["scoped_stale_summary_ids"] == ["beta"]


def test_build_topic_overview_payload_includes_production_summary(temp_vault):
    from openclaw_pipeline.ui.view_models import build_topic_overview_payload

    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Source Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: source-deep-dive
title: Source Deep Dive
type: deep_dive
date: 2026-04-13
---

# Source Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    _seed_truth_store(temp_vault)

    payload = build_topic_overview_payload(temp_vault, "alpha")

    assert payload["production_summary"]["object_count"] == 2
    assert payload["production_summary"]["counts"]["deep_dives"] == 0
    assert payload["production_summary"]["counts"]["atlas_pages"] == 1
    assert payload["production_summary"]["counts"]["source_notes"] == 0
    assert payload["production_summary"]["top_deep_dives"] == []
    assert any(signal["code"] == "missing_source_notes" for signal in payload["production_summary"]["signals"])
    assert any(signal["code"] == "missing_deep_dives" for signal in payload["production_summary"]["signals"])


def test_build_event_dossier_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    _seed_truth_store(temp_vault)

    payload = build_event_dossier_payload(temp_vault)

    assert payload["screen"] == "event/dossier"
    assert payload["event_count"] == 3
    assert payload["dates"] == ["2026-04-13"]
    assert payload["events"][0]["object_id"] == "alpha"
    assert payload["events"][0]["event_kind"] == "dated_note"
    assert payload["events"][0]["event_label"] == "Dated Note"
    assert payload["events"][0]["timeline_anchor_kind"] == "note"
    assert payload["events"][0]["timeline_anchor_label"] == "Alpha"
    assert payload["events"][0]["semantic_role"] == "note_date_projection"
    assert payload["cluster_sections"][0]["date"] == "2026-04-13"
    assert payload["event_type_counts"] == {"dated_note": 3}
    assert payload["limit"] == 50
    assert payload["is_limited"] is True
    assert payload["timeline_contract"]["timeline_kind"] == "dated_note_projection"
    assert payload["timeline_contract"]["row_type_counts"] == {"page_date": 3}
    assert payload["timeline_contract"]["semantic_roles"] == {"note_date_projection": 3}
    assert "dated notes projected from indexed pages" in payload["model_notes"][0]
    assert payload["review_context"]["object_count"] == 3
    assert payload["review_context"]["open_contradiction_count"] == 1
    assert payload["events"][0]["review_links"]["contradictions_path"] == "/contradictions?q=alpha"
    assert payload["events"][0]["review_links"]["summaries_path"] == "/summaries?q=alpha"
    assert payload["review_history"] == []
    assert payload["scoped_open_contradiction_ids"]


def test_build_cluster_browser_payload(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.ui.view_models import build_cluster_browser_payload

    _seed_truth_store(temp_vault)
    gamma = temp_vault / "10-Knowledge" / "Evergreen" / "Gamma.md"
    delta = temp_vault / "10-Knowledge" / "Evergreen" / "Delta.md"
    gamma.write_text(
        """---
note_id: gamma
title: Gamma
type: evergreen
date: 2026-04-13
---

# Gamma

Links to [[delta]].
""",
        encoding="utf-8",
    )
    delta.write_text(
        """---
note_id: delta
title: Delta
type: evergreen
date: 2026-04-13
---

# Delta

Delta extends Gamma.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_cluster_browser_payload(temp_vault)

    assert payload["screen"] == "graph/clusters"
    assert payload["count"] >= 2
    assert payload["cluster_kind_counts"] == {"relation_component": payload["count"]}
    assert payload["items"][0]["center_object_path"].startswith("/object?id=")
    assert payload["items"][0]["member_count"] >= 2
    assert payload["items"][0]["members"][0]["object_id"]
    assert payload["items"][0]["priority_band"] == "attention"
    assert payload["items"][0]["priority_reason"]
    assert payload["items"][0]["display_title"].startswith("Contradiction cluster around")
    assert payload["items"][0]["relation_pattern_preview"]
    assert payload["items"][0]["priority_score"] > payload["items"][1]["priority_score"]
    assert "alpha" in payload["items"][0]["member_object_ids"]


def test_build_cluster_browser_payload_respects_requested_pack(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.ui.view_models import build_cluster_browser_payload

    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Target.md"
    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-10
---

# Source Note

Links to [[target-note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: target-note
title: Target Note
type: evergreen
date: 2026-04-10
---

# Target Note
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_cluster_browser_payload(temp_vault, pack_name="default-knowledge")

    assert payload["requested_pack"] == "default-knowledge"
    assert payload["items"]
    assert all(item["pack"] == "default-knowledge" for item in payload["items"])


def test_build_cluster_browser_payload_includes_related_cluster_summary(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.ui.view_models import build_cluster_browser_payload

    _seed_truth_store(temp_vault)
    gamma = temp_vault / "10-Knowledge" / "Evergreen" / "Gamma.md"
    delta = temp_vault / "10-Knowledge" / "Evergreen" / "Delta.md"
    gamma.write_text(
        """---
note_id: gamma
title: Gamma
type: evergreen
date: 2026-04-13
---

# Gamma

Gamma links to [[delta]].
""",
        encoding="utf-8",
    )
    delta.write_text(
        """---
note_id: delta
title: Delta
type: evergreen
date: 2026-04-13
---

# Delta
""",
        encoding="utf-8",
    )
    shared_source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Shared Deep Dive_深度解读.md"
    shared_source.parent.mkdir(parents=True, exist_ok=True)
    shared_source.write_text(
        """---
note_id: shared-deep-dive
title: Shared Deep Dive
type: deep_dive
date: 2026-04-13
---

# Shared Deep Dive

Mentions [[alpha]], [[beta]], [[gamma]], and [[delta]].
""",
        encoding="utf-8",
    )
    shared_atlas = temp_vault / "10-Knowledge" / "Atlas" / "Shared-Atlas.md"
    shared_atlas.write_text(
        """---
note_id: shared-atlas
title: Shared Atlas
type: moc
date: 2026-04-13
---

# Shared Atlas

- [[alpha]]
- [[beta]]
- [[gamma]]
- [[delta]]
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_cluster_browser_payload(temp_vault)
    item = next(entry for entry in payload["items"] if "alpha" in entry["member_object_ids"])

    assert item["related_cluster_count"] >= 1
    assert item["related_cluster_preview"]
    assert item["neighborhood_score"] > 0
    assert item["neighborhood_reason"]
    assert item["neighborhood_bridge_kind"] == "source_and_atlas_overlap"
    assert item["next_read_title"]
    assert item["next_read_path"].startswith("/cluster?id=")


def test_build_cluster_detail_payload(temp_vault):
    from openclaw_pipeline.truth_api import list_graph_clusters
    from openclaw_pipeline.ui.view_models import build_cluster_detail_payload

    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Source Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: source-deep-dive
title: Source Deep Dive
type: deep_dive
date: 2026-04-13
---

# Source Deep Dive

Mentions [[alpha]] and [[beta]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
- [[beta]]
""",
        encoding="utf-8",
    )
    _seed_truth_store(temp_vault)
    cluster = list_graph_clusters(temp_vault)[0]

    payload = build_cluster_detail_payload(temp_vault, cluster_id=cluster["cluster_id"], pack_name=cluster["pack"])

    assert payload["screen"] == "graph/cluster-detail"
    assert payload["cluster"]["cluster_id"] == cluster["cluster_id"]
    assert payload["cluster"]["detail_path"].startswith("/cluster?id=")
    assert "pack=" in payload["browser_path"]
    assert payload["cluster"]["center_object_path"].startswith("/object?id=")
    assert payload["cluster"]["member_links"][0]["path"].startswith("/object?id=")
    assert payload["edges"]
    assert payload["edges"][0]["source_path"].startswith("/object?id=")
    assert payload["edges"][0]["target_path"].startswith("/object?id=")
    assert payload["structural_label"]["kind"] == "contradiction_cluster"
    assert "Alpha" in payload["structural_label"]["title"]
    assert payload["edge_summary_items"]
    assert payload["edge_summary_items"][0]["edge_family"] == "contradiction"
    assert payload["relation_pattern_items"]
    assert payload["relation_pattern_items"][0]["display_name"].endswith("links")
    assert payload["summary_bullets"]
    assert payload["object_kind_counts"]["evergreen"] == payload["cluster"]["member_count"]
    assert payload["review_context"]["source_note_count"] >= 1
    assert payload["review_context"]["moc_count"] >= 1
    assert payload["open_contradictions"]
    assert payload["stale_summaries"]
    assert payload["top_source_notes"][0]["slug"] == "source-deep-dive"
    assert payload["top_mocs"][0]["slug"] == "atlas-index"


def test_build_cluster_detail_payload_includes_related_clusters(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.truth_api import list_graph_clusters
    from openclaw_pipeline.ui.view_models import build_cluster_detail_payload

    _seed_truth_store(temp_vault)
    gamma = temp_vault / "10-Knowledge" / "Evergreen" / "Gamma.md"
    delta = temp_vault / "10-Knowledge" / "Evergreen" / "Delta.md"
    gamma.write_text(
        """---
note_id: gamma
title: Gamma
type: evergreen
date: 2026-04-13
---

# Gamma

Gamma links to [[delta]].
""",
        encoding="utf-8",
    )
    delta.write_text(
        """---
note_id: delta
title: Delta
type: evergreen
date: 2026-04-13
---

# Delta
""",
        encoding="utf-8",
    )
    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Shared Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: shared-deep-dive
title: Shared Deep Dive
type: deep_dive
date: 2026-04-13
---

# Shared Deep Dive

Mentions [[alpha]], [[beta]], [[gamma]], and [[delta]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Shared-Atlas.md"
    atlas.write_text(
        """---
note_id: shared-atlas
title: Shared Atlas
type: moc
date: 2026-04-13
---

# Shared Atlas

- [[alpha]]
- [[beta]]
- [[gamma]]
- [[delta]]
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    cluster = next(
        item
        for item in list_graph_clusters(temp_vault)
        if "alpha" in {member["object_id"] for member in item["members"]}
    )
    payload = build_cluster_detail_payload(temp_vault, cluster_id=cluster["cluster_id"], pack_name=cluster["pack"])

    assert payload["related_clusters"]
    assert payload["related_clusters"][0]["bridge_band"]
    assert payload["related_clusters"][0]["bridge_kind"] == "source_and_atlas_overlap"
    assert payload["related_clusters"][0]["shared_source_count"] >= 1
    assert payload["related_clusters"][0]["shared_moc_count"] >= 1
    assert payload["related_clusters"][0]["detail_path"].startswith("/cluster?id=")
    assert payload["next_read_cluster"]["detail_path"] == payload["related_clusters"][0]["detail_path"]
    assert payload["next_read_cluster"]["display_title"]
    assert payload["next_read_cluster"]["bridge_kind"] == "source_and_atlas_overlap"


def test_build_event_dossier_payload_includes_provenance(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Source Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: source-deep-dive
title: Source Deep Dive
type: deep_dive
date: 2026-04-13
---

# Source Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    _seed_truth_store(temp_vault)

    payload = build_event_dossier_payload(temp_vault, query="alpha")

    event = next(item for item in payload["events"] if item["object_id"] == "alpha")
    assert event["object_path"] == "/object?id=alpha"
    assert event["provenance"]["evergreen_path"] == "10-Knowledge/Evergreen/Alpha.md"
    assert event["provenance"]["source_notes"][0]["slug"] == "source-deep-dive"
    assert event["provenance"]["mocs"][0]["slug"] == "atlas-index"


def test_build_event_dossier_payload_includes_production_summary(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Source Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: source-deep-dive
title: Source Deep Dive
type: deep_dive
date: 2026-04-13
---

# Source Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    _seed_truth_store(temp_vault)

    payload = build_event_dossier_payload(temp_vault)

    assert payload["production_summary"]["object_count"] == 3
    assert payload["production_summary"]["counts"]["deep_dives"] == 0
    assert payload["production_summary"]["counts"]["atlas_pages"] == 1
    assert payload["production_summary"]["counts"]["source_notes"] == 0
    assert payload["production_summary"]["top_deep_dives"] == []
    assert any(signal["code"] == "missing_source_notes" for signal in payload["production_summary"]["signals"])
    assert any(signal["code"] == "missing_deep_dives" for signal in payload["production_summary"]["signals"])


def test_build_contradiction_browser_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_contradiction_browser_payload

    _seed_truth_store(temp_vault)

    payload = build_contradiction_browser_payload(temp_vault)

    assert payload["screen"] == "truth/contradictions"
    assert payload["count"] == 1
    assert payload["items"][0]["subject_key"] == "alpha"
    assert payload["open_count"] == 1
    assert payload["items"][0]["object_ids"] == ["alpha", "conflict"]
    assert payload["items"][0]["detection_model"] == "page_summary_polarity"
    assert payload["items"][0]["detection_confidence"] == "heuristic"
    assert payload["items"][0]["status_bucket"] == "open"
    assert payload["items"][0]["scope_summary"]["object_count"] == 2
    assert payload["items"][0]["status_explanation"] == "Active contradiction awaiting review."
    assert payload["items"][0]["ranked_evidence"][0]["rank"] == 1
    assert payload["detection_contract"]["model"] == "page_summary_polarity"
    assert payload["detection_contract"]["confidence"] == "heuristic"
    assert payload["detection_contract"]["status_explanations"]["resolved_keep_positive"].startswith("Reviewed")
    assert "page_summary claim polarity" in payload["detection_notes"][0]


def test_build_contradiction_browser_payload_empty_state_explains_zero(temp_vault):
    from openclaw_pipeline.ui.view_models import build_contradiction_browser_payload

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_contradiction_browser_payload(temp_vault)

    assert payload["count"] == 0
    assert "Zero results usually means the current heuristic did not detect a conflict" in payload["empty_state"]


def test_build_contradiction_browser_payload_filters_by_status(temp_vault):
    from openclaw_pipeline.ui.view_models import build_contradiction_browser_payload

    _seed_truth_store(temp_vault)
    _resolve_all_contradictions(temp_vault)

    payload = build_contradiction_browser_payload(temp_vault, status="resolved")

    assert payload["count"] == 1
    assert payload["open_count"] == 0
    assert payload["items"][0]["status"] == "resolved"


def test_build_contradiction_browser_payload_filters_by_query(temp_vault):
    from openclaw_pipeline.ui.view_models import build_contradiction_browser_payload

    _seed_truth_store(temp_vault)

    payload = build_contradiction_browser_payload(temp_vault, query="alp")

    assert payload["count"] == 1
    assert payload["items"][0]["subject_key"] == "alpha"


def test_build_contradiction_browser_payload_includes_provenance(temp_vault):
    from openclaw_pipeline.ui.view_models import build_contradiction_browser_payload

    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Source Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: source-deep-dive
title: Source Deep Dive
type: deep_dive
date: 2026-04-13
---

# Source Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    _seed_truth_store(temp_vault)

    payload = build_contradiction_browser_payload(temp_vault)

    item = payload["items"][0]
    assert item["object_titles"] == {"alpha": "Alpha", "conflict": "Conflict"}
    assert item["provenance"]["source_notes"][0]["slug"] == "source-deep-dive"
    assert item["provenance"]["mocs"][0]["slug"] == "atlas-index"
    assert item["positive_claims"][0]["evidence"]


def test_build_truth_dashboard_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_truth_dashboard_payload

    _seed_truth_store(temp_vault)
    loose_source = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    loose_source.parent.mkdir(parents=True, exist_ok=True)
    loose_source.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note without downstream chain.
""",
        encoding="utf-8",
    )
    thin = temp_vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    thin.write_text(
        """---
note_id: thin-note
title: Thin Note
type: evergreen
date: 2026-04-10
---

# Thin Note

Thin note.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_truth_dashboard_payload(temp_vault)

    assert payload["screen"] == "truth/dashboard"
    assert payload["objects"]["count"] == 4
    assert payload["contradictions"]["count"] == 1
    assert payload["events"]["count"] == 4
    assert payload["stale_summaries"]["count"] >= 1
    assert "thin-note" in [item["object_id"] for item in payload["stale_summaries"]["items"]]
    assert payload["objects"]["items"][0]["object_id"] == "alpha"
    assert payload["priorities"]
    assert payload["production"]["weak_point_count"] >= 1
    assert any(item["kind"] == "production_gap" for item in payload["priorities"])
    production_gap = next(item for item in payload["priorities"] if item["kind"] == "production_gap")
    assert production_gap["path"].startswith("/note?path=50-Inbox%2F03-Processed%2F2026-04%2FLoose%20Source.md")
    assert payload["recent_review_actions"] == []


def test_build_truth_dashboard_payload_uses_total_object_count(temp_vault):
    from openclaw_pipeline.ui.view_models import build_truth_dashboard_payload

    _seed_truth_store(temp_vault)
    for index in range(20):
        extra = temp_vault / "10-Knowledge" / "Evergreen" / f"Extra-{index}.md"
        extra.write_text(
            f"""---
note_id: extra-{index}
title: Extra {index}
type: evergreen
date: 2026-04-13
---

# Extra {index}

Filler note.
""",
            encoding="utf-8",
        )
    rebuild_knowledge_index(temp_vault)

    payload = build_truth_dashboard_payload(temp_vault)

    assert payload["objects"]["count"] == 23
    assert len(payload["objects"]["items"]) == 12


def test_build_signal_browser_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_signal_browser_payload

    _seed_truth_store(temp_vault)
    loose_source = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    loose_source.parent.mkdir(parents=True, exist_ok=True)
    loose_source.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note without downstream chain.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_signal_browser_payload(temp_vault)

    assert payload["screen"] == "signals/browser"
    assert payload["count"] >= 2
    assert payload["type_counts"]["contradiction_open"] >= 1
    assert any(item["signal_type"] == "production_gap" for item in payload["items"])


def test_build_briefing_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_briefing_payload
    from openclaw_pipeline.truth_api import record_review_action

    _seed_truth_store(temp_vault)
    record_review_action(
        temp_vault,
        event_type="ui_summaries_rebuilt",
        slug="alpha",
        payload={
            "object_ids": ["alpha"],
            "objects_rebuilt": 1,
            "rebuilt_object_ids": ["alpha"],
        },
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_briefing_payload(temp_vault)

    assert payload["screen"] == "briefing/intelligence"
    assert payload["recent_signal_count"] >= 1
    assert payload["recent_signals"]
    assert payload["active_topics"]
    assert payload["insights"]
    assert payload["priority_items"]
    assert payload["queue_summary"]["queued_count"] >= 0


def test_build_evolution_browser_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_evolution_browser_payload

    _seed_truth_store(temp_vault)

    payload = build_evolution_browser_payload(temp_vault)

    assert payload["screen"] == "evolution/browser"
    assert payload["count"] >= 1
    assert payload["candidate_items"]
    assert payload["type_counts"]


def test_build_evolution_browser_payload_filters_reviewed_links_by_link_type(temp_vault):
    from openclaw_pipeline.truth_api import list_evolution_candidates, review_evolution_candidate
    from openclaw_pipeline.ui.view_models import build_evolution_browser_payload

    _seed_truth_store(temp_vault)
    legacy = temp_vault / "10-Knowledge" / "Evergreen" / "Legacy.md"
    legacy.write_text(
        """---
note_id: legacy-note
title: Legacy Note
type: evergreen
date: 2026-04-01
---

# Legacy Note

Legacy note.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Legacy Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: legacy-dive
title: Legacy Dive
type: deep_dive
date: 2026-04-10
---

# Legacy Dive

This note supersedes [[legacy-note]] and confirms the migration path.
""",
        encoding="utf-8",
    )
    (temp_vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    (temp_vault / "60-Logs" / "pipeline.jsonl").write_text(
        """{"event_type":"evergreen_auto_promoted","concept":"legacy-note","source":"Legacy Dive_深度解读.md","mutation":{"target_slug":"legacy-note"}}
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    items = list_evolution_candidates(temp_vault)
    challenge = next(item for item in items if item["link_type"] == "challenges")
    replace_item = next(item for item in items if item["link_type"] == "replaces")
    review_evolution_candidate(temp_vault, evolution_id=challenge["evolution_id"], status="accepted")
    review_evolution_candidate(temp_vault, evolution_id=replace_item["evolution_id"], status="accepted")

    payload = build_evolution_browser_payload(temp_vault, status="all", link_type="challenges")

    assert payload["accepted_links"]
    assert {item["link_type"] for item in payload["accepted_links"]} == {"challenges"}


def test_build_object_page_payload_includes_evolution_section(temp_vault):
    from openclaw_pipeline.ui.view_models import build_object_page_payload

    _seed_truth_store(temp_vault)

    payload = build_object_page_payload(temp_vault, "alpha")

    assert "evolution" in payload
    assert payload["evolution"]["candidate_count"] >= 1


def test_build_topic_overview_payload_includes_evolution_section(temp_vault):
    from openclaw_pipeline.ui.view_models import build_topic_overview_payload

    _seed_truth_store(temp_vault)

    payload = build_topic_overview_payload(temp_vault, "alpha")

    assert "evolution" in payload
    assert payload["evolution"]["candidate_count"] >= 1


def test_build_event_dossier_payload_filters_by_query(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    _seed_truth_store(temp_vault)

    payload = build_event_dossier_payload(temp_vault, query="beta")

    assert payload["event_count"] == 1
    assert [item["object_id"] for item in payload["events"]] == ["beta"]


def test_build_event_dossier_payload_groups_rows_into_event_clusters(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.

## 2026-04-13

Shipped the local-first harness update.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_event_dossier_payload(temp_vault, query="alpha")

    assert payload["event_count"] == 2
    assert payload["cluster_count"] == 1
    assert payload["cluster_sections"][0]["clusters"][0]["object_id"] == "alpha"
    assert payload["cluster_sections"][0]["clusters"][0]["row_count"] == 2
    assert payload["cluster_sections"][0]["clusters"][0]["row_types"] == ["heading_date", "page_date"]


def test_build_atlas_browser_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_atlas_browser_payload

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_atlas_browser_payload(temp_vault)

    assert payload["screen"] == "atlas/browser"
    assert payload["count"] == 1
    assert payload["items"][0]["slug"] == "atlas-index"
    assert payload["items"][0]["members"][0]["object_id"] == "alpha"
    assert payload["items"][0]["member_count"] == 1
    assert payload["items"][0]["preview_titles"] == ["Alpha"]
    assert payload["limit"] == 50
    assert payload["is_limited"] is True


def test_build_derivation_browser_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_derivation_browser_payload

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: deep-dive
title: Deep Dive
type: deep_dive
date: 2026-04-13
---

# Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Deep Dive_深度解读.md","mutation":{"target_slug":"alpha"}}\n',
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_derivation_browser_payload(temp_vault)

    assert payload["screen"] == "derivations/browser"
    assert payload["count"] == 1
    assert payload["items"][0]["slug"] == "deep-dive"
    assert payload["items"][0]["derived_objects"][0]["object_id"] == "alpha"
    assert payload["items"][0]["derived_object_count"] == 1
    assert payload["items"][0]["preview_titles"] == ["Alpha"]
    assert payload["items"][0]["source_notes"] == []
    assert payload["limit"] == 50
    assert payload["is_limited"] is True


def test_build_derivation_browser_payload_includes_chain_context(temp_vault):
    from openclaw_pipeline.ui.view_models import build_derivation_browser_payload

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness
source: https://example.com/harness
---

Processed source note.
""",
        encoding="utf-8",
    )
    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: deep-dive
title: Deep Dive
type: deep_dive
source: https://example.com/harness
date: 2026-04-13
---

# Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Deep Dive_深度解读.md","mutation":{"target_slug":"alpha"}}\n',
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_derivation_browser_payload(temp_vault)

    assert payload["items"][0]["source_notes"][0]["path"] == "50-Inbox/03-Processed/2026-04/Harness.md"
    assert payload["items"][0]["atlas_pages"][0]["slug"] == "atlas-index"


def test_build_atlas_browser_payload_includes_chain_context(temp_vault):
    from openclaw_pipeline.ui.view_models import build_atlas_browser_payload

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness
source: https://example.com/harness
---

Processed source note.
""",
        encoding="utf-8",
    )
    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: deep-dive
title: Deep Dive
type: deep_dive
source: https://example.com/harness
date: 2026-04-13
---

# Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Deep Dive_深度解读.md","mutation":{"target_slug":"alpha"}}\n',
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_atlas_browser_payload(temp_vault)

    assert payload["items"][0]["source_notes"][0]["path"] == "50-Inbox/03-Processed/2026-04/Harness.md"
    assert payload["items"][0]["deep_dives"][0]["slug"] == "deep-dive"


def test_build_production_browser_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_production_browser_payload

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness
source: https://example.com/harness
---

Processed source note.
""",
        encoding="utf-8",
    )
    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: deep-dive
title: Deep Dive
type: deep_dive
source: https://example.com/harness
date: 2026-04-13
---

# Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        "\n".join(
            [
                '{"event_type":"article_processed","file":"Harness.md","output":"'
                + str(deep_dive)
                + '"}',
                '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Deep Dive_深度解读.md","mutation":{"target_slug":"alpha"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_production_browser_payload(temp_vault)

    assert payload["screen"] == "production/browser"
    assert payload["counts"]["source_notes"] == 1
    assert payload["counts"]["deep_dives"] == 1
    assert any(item["stage_label"] == "source_note" for item in payload["items"])
    assert any(item["stage_label"] == "deep_dive" for item in payload["items"])
    assert payload["limit"] == 50
    assert payload["is_limited"] is True


def test_build_production_browser_payload_surfaces_weak_points(temp_vault):
    from openclaw_pipeline.ui.view_models import build_production_browser_payload

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note without downstream chain.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_production_browser_payload(temp_vault)

    assert payload["weak_points"]
    assert payload["weak_points"][0]["title"] == "Loose Source"
    assert "deep dives" in payload["weak_points"][0]["missing"]


def test_build_derivation_browser_payload_filters_stale_object_ids(temp_vault):
    from openclaw_pipeline.ui.view_models import build_derivation_browser_payload
    from openclaw_pipeline.runtime import VaultLayout

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha

Alpha supports local-first execution.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: deep-dive
title: Deep Dive
type: deep_dive
date: 2026-04-13
---

# Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        "\n".join(
            [
                '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Deep Dive_深度解读.md","mutation":{"target_slug":"alpha"}}',
                '{"event_type":"evergreen_auto_promoted","concept":"stale-object","source":"Deep Dive_深度解读.md","mutation":{"target_slug":"stale-object"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_derivation_browser_payload(temp_vault)

    assert [item["object_id"] for item in payload["items"][0]["derived_objects"]] == ["alpha"]


def test_build_stale_summary_browser_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_stale_summary_browser_payload

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    note.write_text(
        """---
note_id: thin-note
title: Thin Note
type: evergreen
date: 2026-04-10
---

# Thin Note

Thin note.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_stale_summary_browser_payload(temp_vault)

    assert payload["screen"] == "truth/stale-summaries"
    assert payload["count"] == 1
    assert payload["items"][0]["object_id"] == "thin-note"
    assert "summary_too_short" in payload["items"][0]["reason_codes"]
    assert payload["items"][0]["latest_event_date"] == "2026-04-10"
    assert payload["review_history"] == []


def test_build_object_page_payload_includes_review_history(temp_vault):
    from openclaw_pipeline.truth_api import record_review_action
    from openclaw_pipeline.ui.view_models import build_object_page_payload

    _seed_truth_store(temp_vault)
    record_review_action(
        temp_vault,
        event_type="ui_contradictions_resolved",
        slug="alpha",
        payload={
            "object_ids": ["alpha"],
            "contradiction_ids": ["contradiction::alpha"],
            "status": "resolved_keep_positive",
            "note": "Reviewed in UI",
            "rebuilt_object_ids": ["alpha"],
        },
    )

    payload = build_object_page_payload(temp_vault, "alpha")

    assert payload["review_history"][0]["event_type"] == "ui_contradictions_resolved"
    assert payload["review_history"][0]["note"] == "Reviewed in UI"


def test_build_object_page_payload_exposes_quick_maintenance_state(temp_vault):
    from openclaw_pipeline.ui.view_models import build_object_page_payload

    _seed_truth_store(temp_vault)

    payload = build_object_page_payload(temp_vault, "alpha")

    assert payload["open_contradiction_ids"]
    assert payload["stale_summary_details"] == []


def test_build_stale_summary_browser_payload_includes_review_context(temp_vault):
    from openclaw_pipeline.ui.view_models import build_stale_summary_browser_payload

    note = temp_vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    note.write_text(
        """---
note_id: thin-note
title: Thin Note
type: evergreen
date: 2026-04-10
---

# Thin Note

Thin note.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_stale_summary_browser_payload(temp_vault)

    assert payload["review_context"]["object_count"] == 1
    assert payload["review_context"]["stale_summary_count"] == 1


def test_build_event_dossier_payload_applies_limit_before_materializing(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    _seed_truth_store(temp_vault)

    payload = build_event_dossier_payload(temp_vault, limit=2)

    assert payload["event_count"] == 2
    assert len(payload["events"]) == 2


def test_build_event_dossier_payload_orders_latest_first(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    older = temp_vault / "10-Knowledge" / "Evergreen" / "Older.md"
    newer = temp_vault / "10-Knowledge" / "Evergreen" / "Newer.md"
    older.write_text(
        """---
note_id: older
title: Older
type: evergreen
date: 2026-04-10
---

# Older

Older event.
""",
        encoding="utf-8",
    )
    newer.write_text(
        """---
note_id: newer
title: Newer
type: evergreen
date: 2026-04-13
---

# Newer

Newer event.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_event_dossier_payload(temp_vault)

    assert payload["dates"][0] == "2026-04-13"
    assert payload["events"][0]["object_id"] == "newer"


def test_build_object_page_payload_handles_missing_summary(temp_vault):
    from openclaw_pipeline.ui.view_models import build_object_page_payload
    from openclaw_pipeline.runtime import VaultLayout

    _seed_truth_store(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM compiled_summaries WHERE object_id = ?", ("alpha",))
        conn.commit()

    payload = build_object_page_payload(temp_vault, "alpha")

    assert payload["summary"] is None
    assert payload["claim_count"] == 1


def test_build_note_page_payload_includes_production_chain(temp_vault):
    from openclaw_pipeline.ui.view_models import build_note_page_payload

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness
source: https://example.com/harness
---

Processed source note.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Harness_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: harness-deep-dive
title: Harness Deep Dive
type: deep_dive
source: https://example.com/harness
date: 2026-04-13
---

# Harness Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    evergreen.parent.mkdir(parents=True, exist_ok=True)
    evergreen.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        "\n".join(
            [
                '{"event_type":"article_processed","file":"Harness.md","output":"'
                + str(deep_dive)
                + '"}',
                '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Harness_深度解读.md","mutation":{"target_slug":"alpha"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_note_page_payload(
        temp_vault,
        note_path="50-Inbox/03-Processed/2026-04/Harness.md",
    )

    assert payload["production_chain"]["note"]["path"] == "50-Inbox/03-Processed/2026-04/Harness.md"
    assert [item["title"] for item in payload["production_chain"]["deep_dives"]] == ["Harness Deep Dive"]
    assert [item["object_id"] for item in payload["production_chain"]["objects"]] == ["alpha"]
    assert [item["slug"] for item in payload["production_chain"]["atlas_pages"]] == ["atlas-index"]


def test_build_object_page_payload_includes_production_chain(temp_vault):
    from openclaw_pipeline.ui.view_models import build_object_page_payload

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness
source: https://example.com/harness
---

Processed source note.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Harness_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: harness-deep-dive
title: Harness Deep Dive
type: deep_dive
source: https://example.com/harness
date: 2026-04-13
---

# Harness Deep Dive

Mentions [[alpha]].
""",
        encoding="utf-8",
    )
    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    evergreen.parent.mkdir(parents=True, exist_ok=True)
    evergreen.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
---

# Alpha
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Atlas Index.md"
    atlas.write_text(
        """---
note_id: atlas-index
title: Atlas Index
type: moc
date: 2026-04-13
---

# Atlas Index

- [[alpha]]
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        '{"event_type":"evergreen_auto_promoted","concept":"alpha","source":"Harness_深度解读.md","mutation":{"target_slug":"alpha"}}\n',
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    payload = build_object_page_payload(temp_vault, "alpha")

    assert [item["slug"] for item in payload["production_chain"]["deep_dives"]] == ["harness-deep-dive"]
    assert [item["path"] for item in payload["production_chain"]["source_notes"]] == ["50-Inbox/03-Processed/2026-04/Harness.md"]
    assert [item["slug"] for item in payload["production_chain"]["atlas_pages"]] == ["atlas-index"]
