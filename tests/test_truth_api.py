from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from openclaw_pipeline.knowledge_index import rebuild_knowledge_index


def _seed_truth_vault(temp_vault):
    source = temp_vault / "10-Knowledge" / "Evergreen" / "Source.md"
    target = temp_vault / "10-Knowledge" / "Evergreen" / "Target.md"
    negative = temp_vault / "10-Knowledge" / "Evergreen" / "Negative.md"

    source.write_text(
        """---
note_id: source-note
title: Source Note
type: evergreen
date: 2026-04-13
---

# Source Note

Agent harness supports local-first execution for operators.

Links to [[target-note]].
""",
        encoding="utf-8",
    )
    target.write_text(
        """---
note_id: target-note
title: Target Note
type: evergreen
date: 2026-04-13
---

# Target Note

Target note captures downstream effects.
""",
        encoding="utf-8",
    )
    negative.write_text(
        """---
note_id: negative-note
title: Negative Note
type: evergreen
date: 2026-04-13
---

# Negative Note

Agent harness does not support local-first execution for operators.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    return temp_vault


def test_truth_api_lists_objects(temp_vault):
    from openclaw_pipeline.truth_api import list_objects

    vault = _seed_truth_vault(temp_vault)

    objects = list_objects(vault)

    assert [item["object_id"] for item in objects] == [
        "negative-note",
        "source-note",
        "target-note",
    ]
    assert objects[1]["title"] == "Source Note"
    assert objects[1]["object_kind"] == "evergreen"


def test_truth_api_reads_signal_and_action_ledgers_without_knowledge_db(temp_vault):
    from openclaw_pipeline.truth_api import list_action_queue, list_signals

    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "knowledge.db").write_bytes(b"not-a-real-sqlite-db")
    (logs_dir / "signals.jsonl").write_text(
        json.dumps(
            {
                "signal_id": "source_needs_deep_dive::demo",
                "signal_type": "source_needs_deep_dive",
                "detected_at": "2026-04-15T00:00:00Z",
                "status": "active",
                "title": "Create deep dive for Demo Source",
                "detail": "Demo source is missing a deep dive.",
                "source_path": "/note?path=50-Inbox%2F03-Processed%2FDemo.md",
                "object_ids": [],
                "note_paths": ["50-Inbox/03-Processed/Demo.md"],
                "recommended_action": {
                    "kind": "deep_dive_workflow",
                    "label": "Create deep dive",
                    "path": "/note?path=50-Inbox%2F03-Processed%2FDemo.md",
                    "executable": False,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (logs_dir / "actions.jsonl").write_text(
        json.dumps(
            {
                "action_id": "action::demo",
                "action_kind": "deep_dive_workflow",
                "source_signal_id": "source_needs_deep_dive::demo",
                "title": "Create deep dive for Demo Source",
                "target_ref": "50-Inbox/03-Processed/Demo.md",
                "status": "queued",
                "created_at": "2026-04-15T00:00:01Z",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    signals = list_signals(temp_vault)
    actions = list_action_queue(temp_vault)

    assert signals[0]["signal_id"] == "source_needs_deep_dive::demo"
    assert signals[0]["recommended_action"]["queue_status"] == "queued"
    assert actions[0]["action_id"] == "action::demo"


def test_truth_api_avoids_datetime_utc_import_for_python_310_compatibility():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openclaw_pipeline"
        / "truth_api.py"
    ).read_text(encoding="utf-8")

    assert "from datetime import UTC" not in source
    assert "datetime.now(UTC)" not in source
    assert "datetime.UTC" not in source


def test_truth_api_reads_review_actions_from_jsonl_without_knowledge_db(temp_vault):
    from openclaw_pipeline.truth_api import list_evolution_review_actions, list_review_actions

    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "knowledge.db").write_bytes(b"not-a-real-sqlite-db")
    (logs_dir / "review-actions.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-15T00:00:00Z",
                        "session_id": "ovp-ui",
                        "event_type": "ui_evolution_reviewed",
                        "slug": "agent-harness",
                        "object_ids": ["source-note"],
                        "evolution_id": "evolution::demo",
                        "link_type": "challenges",
                        "status": "accepted",
                        "note": "accepted",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-15T00:00:10Z",
                        "session_id": "ovp-ui",
                        "event_type": "ui_summaries_rebuilt",
                        "slug": "source-note",
                        "object_ids": ["source-note"],
                        "rebuilt_object_ids": ["source-note"],
                        "objects_rebuilt": 1,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    review_actions = list_review_actions(temp_vault)
    evolution_actions = list_evolution_review_actions(temp_vault)

    assert review_actions[0]["event_type"] == "ui_summaries_rebuilt"
    assert evolution_actions[0]["evolution_id"] == "evolution::demo"


def test_note_date_text_returns_empty_string_when_frontmatter_date_missing(temp_vault):
    from openclaw_pipeline.truth_api import _note_date_text

    note = temp_vault / "10-Knowledge" / "Evergreen" / "NoDate.md"
    note.write_text(
        """---
note_id: no-date
title: No Date
type: evergreen
---

# No Date
""",
        encoding="utf-8",
    )

    assert _note_date_text(temp_vault, "10-Knowledge/Evergreen/NoDate.md") == ""


def test_truth_api_returns_object_detail_with_claims_relations_and_summary(temp_vault):
    from openclaw_pipeline.truth_api import get_object_detail

    vault = _seed_truth_vault(temp_vault)

    detail = get_object_detail(vault, "source-note")

    assert detail["object"]["object_id"] == "source-note"
    assert detail["summary"]["summary_text"] == "Agent harness supports local-first execution for operators."
    assert detail["claims"][0]["claim_kind"] == "page_summary"
    assert detail["relations"][0]["target_object_id"] == "target-note"
    assert detail["evidence"][0]["evidence_kind"] == "body_summary"
    assert detail["contradictions"][0]["subject_key"] == "agent harness"


def test_truth_api_filters_truth_rows_by_pack_name(temp_vault):
    from openclaw_pipeline.truth_api import get_object_detail, list_objects

    vault = _seed_truth_vault(temp_vault)
    db_path = vault / "60-Logs" / "knowledge.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO objects (pack, object_id, object_kind, title, canonical_path, source_slug)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "default-knowledge",
                "source-note",
                "evergreen",
                "Default Source Note",
                "10-Knowledge/Evergreen/Source.md",
                "source-note",
            ),
        )
        conn.execute(
            """
            INSERT INTO claims (pack, claim_id, object_id, claim_kind, claim_text, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "default-knowledge",
                "source-note::default-pack",
                "source-note",
                "page_summary",
                "Default-knowledge source summary.",
                1.0,
            ),
        )
        conn.execute(
            """
            INSERT INTO compiled_summaries (pack, object_id, summary_text, source_slug)
            VALUES (?, ?, ?, ?)
            """,
            (
                "default-knowledge",
                "source-note",
                "Default-knowledge source summary.",
                "source-note",
            ),
        )
        conn.commit()

    research_objects = list_objects(vault, pack_name="research-tech")
    default_objects = list_objects(vault, pack_name="default-knowledge")
    research_detail = get_object_detail(vault, "source-note", pack_name="research-tech")
    default_detail = get_object_detail(vault, "source-note", pack_name="default-knowledge")

    assert research_objects[1]["title"] == "Source Note"
    assert default_objects == [
        {
            "object_id": "source-note",
            "object_kind": "evergreen",
            "title": "Default Source Note",
            "canonical_path": "10-Knowledge/Evergreen/Source.md",
            "source_slug": "source-note",
            "pack": "default-knowledge",
        }
    ]
    assert research_detail["object"]["title"] == "Source Note"
    assert default_detail["object"]["title"] == "Default Source Note"
    assert default_detail["summary"]["summary_text"] == "Default-knowledge source summary."


def test_truth_api_lists_research_graph_clusters(temp_vault):
    from openclaw_pipeline.truth_api import list_graph_clusters

    vault = _seed_truth_vault(temp_vault)

    clusters = list_graph_clusters(vault, pack_name="research-tech")

    assert len(clusters) >= 1
    assert clusters[0]["pack"] == "research-tech"
    assert clusters[0]["cluster_kind"] == "relation_component"
    assert "source-note" in clusters[0]["member_object_ids"]
    assert "target-note" in clusters[0]["member_object_ids"]


def test_truth_api_does_not_fallback_when_requested_pack_is_materialized(temp_vault, monkeypatch):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.truth_api import get_object_detail, list_graph_clusters, list_objects
    from openclaw_pipeline.truth_store import TruthStoreProjection

    vault = _seed_truth_vault(temp_vault)

    class Spec:
        pack = "research-tech"
        name = "research-tech-default"

    def fake_execute_truth_projection_builder(*, vault_dir, page_rows, link_rows, pack_name=None):
        assert vault_dir == vault
        assert pack_name == "default-knowledge"
        return (
            Spec(),
            TruthStoreProjection(
                objects=[
                    (
                        "default-knowledge",
                        "source-note",
                        "evergreen",
                        "Default Source Note",
                        "10-Knowledge/Evergreen/Source.md",
                        "source-note",
                    )
                ],
                claims=[],
                claim_evidence=[],
                relations=[],
                compiled_summaries=[],
                contradictions=[],
                graph_edges=[],
                graph_clusters=[],
            ),
        )

    monkeypatch.setattr(
        "openclaw_pipeline.knowledge_index.execute_truth_projection_builder",
        fake_execute_truth_projection_builder,
    )

    rebuild_knowledge_index(vault, pack_name="default-knowledge")

    research_clusters = list_graph_clusters(vault, pack_name="research-tech")
    default_clusters = list_graph_clusters(vault, pack_name="default-knowledge")
    default_objects = list_objects(vault, pack_name="default-knowledge")
    default_detail = get_object_detail(vault, "source-note", pack_name="default-knowledge")

    assert research_clusters
    assert default_clusters == []
    assert default_objects == [
        {
            "object_id": "source-note",
            "object_kind": "evergreen",
            "title": "Default Source Note",
            "canonical_path": "10-Knowledge/Evergreen/Source.md",
            "source_slug": "source-note",
            "pack": "default-knowledge",
        }
    ]
    assert default_detail["object"]["title"] == "Default Source Note"
    assert default_detail["object"]["pack"] == "default-knowledge"


def test_truth_api_lists_contradictions(temp_vault):
    from openclaw_pipeline.truth_api import list_contradictions

    vault = _seed_truth_vault(temp_vault)

    items = list_contradictions(vault)

    assert len(items) == 1
    assert items[0]["subject_key"] == "agent harness"
    assert items[0]["status"] == "open"
    assert items[0]["status_explanation"] == "Active contradiction awaiting review."
    assert items[0]["scope_summary"]["object_count"] == 2
    assert items[0]["scope_summary"]["positive_claim_count"] == 1
    assert items[0]["scope_summary"]["negative_claim_count"] == 1
    assert items[0]["ranked_evidence"][0]["rank"] == 1
    assert items[0]["positive_claim_ids"]
    assert items[0]["negative_claim_ids"]


def test_truth_api_filters_contradictions_by_query(temp_vault):
    from openclaw_pipeline.truth_api import list_contradictions

    vault = _seed_truth_vault(temp_vault)

    items = list_contradictions(vault, query="agent")

    assert len(items) == 1
    assert items[0]["subject_key"] == "agent harness"


def test_truth_api_filters_contradictions_by_resolved_status_after_overrides(temp_vault):
    from openclaw_pipeline.truth_api import list_contradictions, record_review_action

    vault = _seed_truth_vault(temp_vault)
    positive = vault / "10-Knowledge" / "Evergreen" / "Zeta Positive.md"
    negative = vault / "10-Knowledge" / "Evergreen" / "Zeta Negative.md"
    positive.write_text(
        """---
note_id: zeta-positive
title: Zeta Positive
type: evergreen
date: 2026-04-13
---

# Zeta Positive

Zeta platform supports high-trust execution for operators.
""",
        encoding="utf-8",
    )
    negative.write_text(
        """---
note_id: zeta-negative
title: Zeta Negative
type: evergreen
date: 2026-04-13
---

# Zeta Negative

Zeta platform does not support high-trust execution for operators.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    contradiction = next(item for item in list_contradictions(vault, limit=10) if item["subject_key"] == "zeta platform")
    record_review_action(
        vault,
        event_type="ui_contradictions_resolved",
        slug="zeta-positive",
        payload={
            "object_ids": ["zeta-negative", "zeta-positive"],
            "contradiction_ids": [contradiction["contradiction_id"]],
            "status": "dismissed",
            "note": "",
            "rebuilt_object_ids": [],
        },
    )

    items = list_contradictions(vault, status="resolved", limit=1)

    assert len(items) == 1
    assert items[0]["subject_key"] == "zeta platform"
    assert items[0]["status"] == "dismissed"


def test_truth_api_lists_evolution_candidates_from_open_contradictions(temp_vault):
    from openclaw_pipeline.truth_api import list_evolution_candidates

    vault = _seed_truth_vault(temp_vault)

    items = list_evolution_candidates(vault)

    assert items
    challenge = next(item for item in items if item["link_type"] == "challenges")
    assert challenge["status"] == "candidate"
    assert challenge["subject_kind"] == "topic"
    assert challenge["subject_id"] == "agent harness"
    assert set(challenge["object_ids"]) == {"negative-note", "source-note"}
    assert challenge["reason_codes"] == ["open_contradiction", "claim_polarity_divergence"]


def test_truth_api_lists_replaces_and_enriches_candidates(temp_vault):
    from openclaw_pipeline.truth_api import list_evolution_candidates

    vault = _seed_truth_vault(temp_vault)
    legacy = vault / "10-Knowledge" / "Evergreen" / "Legacy.md"
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
    deep_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Legacy Dive_深度解读.md"
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
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs" / "pipeline.jsonl").write_text(
        json.dumps(
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "legacy-note",
                "source": "Legacy Dive_深度解读.md",
                "mutation": {"target_slug": "legacy-note"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    items = list_evolution_candidates(vault, query="legacy")

    assert any(item["link_type"] == "replaces" and item["subject_id"] == "legacy-note" for item in items)

    enrich_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Source Enrichment_深度解读.md"
    enrich_dive.parent.mkdir(parents=True, exist_ok=True)
    enrich_dive.write_text(
        """---
note_id: source-enrichment
title: Source Enrichment
type: deep_dive
date: 2026-04-20
---

Source note builds on [[source-note]] with more deployment detail.
""",
        encoding="utf-8",
    )
    (vault / "60-Logs" / "pipeline.jsonl").write_text(
        (vault / "60-Logs" / "pipeline.jsonl").read_text(encoding="utf-8")
        + json.dumps(
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "source-note",
                "source": "Source Enrichment_深度解读.md",
                "mutation": {"target_slug": "source-note"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    items = list_evolution_candidates(vault, query="source-note")

    assert any(item["link_type"] in {"enriches", "confirms"} and item["subject_id"] == "source-note" for item in items)


def test_truth_api_expresses_all_four_evolution_link_types(temp_vault):
    from openclaw_pipeline.truth_api import list_evolution_candidates

    vault = _seed_truth_vault(temp_vault)
    legacy = vault / "10-Knowledge" / "Evergreen" / "Legacy.md"
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
    replace_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Legacy Replace_深度解读.md"
    replace_dive.parent.mkdir(parents=True, exist_ok=True)
    replace_dive.write_text(
        """---
note_id: legacy-replace
title: Legacy Replace
type: deep_dive
date: 2026-04-10
---

# Legacy Replace

This note supersedes [[legacy-note]] and instead recommends the new path.
""",
        encoding="utf-8",
    )
    enrich_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Source Enrichment_深度解读.md"
    enrich_dive.write_text(
        """---
note_id: source-enrichment
title: Source Enrichment
type: deep_dive
date: 2026-04-20
---

Source note builds on [[source-note]] with more deployment detail.
""",
        encoding="utf-8",
    )
    confirm_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Source Confirmation_深度解读.md"
    confirm_dive.write_text(
        """---
note_id: source-confirmation
title: Source Confirmation
type: deep_dive
date: 2026-04-22
---

Source note confirms the local-first rollout guidance from independent testing.
""",
        encoding="utf-8",
    )
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs" / "pipeline.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "evergreen_auto_promoted",
                        "concept": "legacy-note",
                        "source": "Legacy Replace_深度解读.md",
                        "mutation": {"target_slug": "legacy-note"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event_type": "evergreen_auto_promoted",
                        "concept": "source-note",
                        "source": "Source Enrichment_深度解读.md",
                        "mutation": {"target_slug": "source-note"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event_type": "evergreen_auto_promoted",
                        "concept": "source-note",
                        "source": "Source Confirmation_深度解读.md",
                        "mutation": {"target_slug": "source-note"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    items = list_evolution_candidates(vault)
    link_types = {item["link_type"] for item in items}

    assert {"replaces", "enriches", "confirms", "challenges"}.issubset(link_types)


def test_truth_api_scopes_evolution_candidate_traceability_to_requested_objects(temp_vault, monkeypatch):
    from openclaw_pipeline import truth_api

    vault = _seed_truth_vault(temp_vault)
    observed_object_ids: list[str] = []
    original = truth_api.get_object_traceability

    def counted_get_object_traceability(vault_dir, object_id):
        observed_object_ids.append(object_id)
        return original(vault_dir, object_id)

    monkeypatch.setattr(truth_api, "get_object_traceability", counted_get_object_traceability)

    items = truth_api.list_evolution_candidates(vault, object_ids=["source-note"])

    assert items
    assert set(observed_object_ids) == {"source-note"}
    assert all("source-note" in item["object_ids"] for item in items)


def test_truth_api_ignores_missing_objects_in_evolution_object_pool(temp_vault):
    from openclaw_pipeline.truth_api import list_evolution_candidates

    vault = _seed_truth_vault(temp_vault)
    deep_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Ghost Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: ghost-dive
title: Ghost Dive
type: deep_dive
date: 2026-04-14
---

# Ghost Dive
""",
        encoding="utf-8",
    )
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs" / "pipeline.jsonl").write_text(
        json.dumps(
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "ghost-object",
                "source": "Ghost Dive_深度解读.md",
                "mutation": {"target_slug": "ghost-object"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    items = list_evolution_candidates(vault)

    assert all("ghost-object" not in item["object_ids"] for item in items)


def test_truth_api_reviews_evolution_candidate_and_lists_links(temp_vault):
    from openclaw_pipeline.truth_api import list_evolution_candidates, list_evolution_links, review_evolution_candidate

    vault = _seed_truth_vault(temp_vault)
    candidate = next(item for item in list_evolution_candidates(vault) if item["link_type"] == "challenges")

    payload = review_evolution_candidate(
        vault,
        evolution_id=candidate["evolution_id"],
        status="accepted",
        note="Accepted in review",
        link_type="challenges",
    )
    links = list_evolution_links(vault, status="accepted")

    assert payload["accepted_count"] == 1
    assert links
    assert links[0]["evolution_id"] == candidate["evolution_id"]
    assert links[0]["status"] == "accepted"


def test_truth_api_reviews_evolution_candidate_beyond_page_limit(temp_vault, monkeypatch):
    from openclaw_pipeline import truth_api

    vault = _seed_truth_vault(temp_vault)
    legacy = vault / "10-Knowledge" / "Evergreen" / "Legacy.md"
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
    deep_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Legacy Dive_深度解读.md"
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
    (vault / "60-Logs").mkdir(parents=True, exist_ok=True)
    (vault / "60-Logs" / "pipeline.jsonl").write_text(
        json.dumps(
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "legacy-note",
                "source": "Legacy Dive_深度解读.md",
                "mutation": {"target_slug": "legacy-note"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    items = truth_api.list_evolution_candidates(vault)
    assert len(items) >= 2
    target = items[-1]
    original = truth_api.list_evolution_candidates

    def capped_list_evolution_candidates(vault_dir, *, limit=100, offset=0, **kwargs):
        effective_limit = 1 if limit is not None else limit
        return original(vault_dir, limit=effective_limit, offset=offset, **kwargs)

    monkeypatch.setattr(truth_api, "list_evolution_candidates", capped_list_evolution_candidates)

    payload = truth_api.review_evolution_candidate(
        vault,
        evolution_id=target["evolution_id"],
        status="accepted",
    )

    assert payload["evolution_ids"] == [target["evolution_id"]]


def test_truth_api_builds_topic_neighborhood(temp_vault):
    from openclaw_pipeline.truth_api import get_topic_neighborhood

    vault = _seed_truth_vault(temp_vault)

    neighborhood = get_topic_neighborhood(vault, "source-note")

    assert neighborhood["center"]["object_id"] == "source-note"
    assert [item["object_id"] for item in neighborhood["neighbors"]] == ["target-note"]
    assert neighborhood["edges"] == [
        {
            "source_object_id": "source-note",
            "target_object_id": "target-note",
            "relation_type": "wikilink",
            "evidence_source_slug": "source-note",
        }
    ]


def test_truth_api_rejects_negative_pagination_inputs(temp_vault):
    from openclaw_pipeline.truth_api import list_contradictions, list_objects

    vault = _seed_truth_vault(temp_vault)

    for kwargs in ({"limit": -1}, {"offset": -1}, {"limit": -1, "offset": -1}):
        try:
            list_objects(vault, **kwargs)
        except ValueError as exc:
            assert "must be >= 0" in str(exc)
        else:
            raise AssertionError(f"Expected ValueError for {kwargs}")

    try:
        list_contradictions(vault, limit=-1)
    except ValueError as exc:
        assert "must be >= 0" in str(exc)
    else:
        raise AssertionError("Expected ValueError for negative contradiction limit")


def test_truth_api_filters_objects_by_query(temp_vault):
    from openclaw_pipeline.truth_api import list_objects

    vault = _seed_truth_vault(temp_vault)

    objects = list_objects(vault, query="target")

    assert [item["object_id"] for item in objects] == ["target-note"]


def test_truth_api_escapes_like_wildcards_in_object_queries(temp_vault):
    from openclaw_pipeline.truth_api import list_objects

    percent = temp_vault / "10-Knowledge" / "Evergreen" / "Percent.md"
    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    percent.write_text(
        """---
note_id: percent%note
title: Percent % Note
type: evergreen
date: 2026-04-13
---

# Percent % Note

Literal percent id.
""",
        encoding="utf-8",
    )
    alpha.write_text(
        """---
note_id: alpha-note
title: Alpha Note
type: evergreen
date: 2026-04-13
---

# Alpha Note

Regular note.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    objects = list_objects(temp_vault, query="%")

    assert [item["title"] for item in objects] == ["Percent % Note"]


def test_truth_api_matches_contradictions_by_exact_object_id_prefix(temp_vault):
    from openclaw_pipeline.truth_api import get_object_detail

    alpha = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    alpha_one = temp_vault / "10-Knowledge" / "Evergreen" / "AlphaOne.md"
    alpha_neg = temp_vault / "10-Knowledge" / "Evergreen" / "AlphaNeg.md"

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
    alpha_one.write_text(
        """---
note_id: alpha-one
title: Alpha One
type: evergreen
date: 2026-04-13
---

# Alpha One

Alpha one supports local-first execution.
""",
        encoding="utf-8",
    )
    alpha_neg.write_text(
        """---
note_id: alpha-one-negative
title: Alpha One Negative
type: evergreen
date: 2026-04-13
---

# Alpha One Negative

Alpha one does not support local-first execution.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(temp_vault)

    detail = get_object_detail(temp_vault, "alpha")

    assert detail["contradictions"] == []


def test_truth_api_searches_objects_and_notes(temp_vault):
    from openclaw_pipeline.truth_api import search_vault_surface

    vault = _seed_truth_vault(temp_vault)
    deep_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Agent Harness_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
title: Agent Harness Deep Dive
source: https://example.com/agent-harness
date: 2026-04-13
type: deep_dive
---

# Agent Harness Deep Dive

Mentions [[source-note]].
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    payload = search_vault_surface(vault, query="agent harness")

    assert payload["query"] == "agent harness"
    assert {item["object_id"] for item in payload["objects"]} == {"negative-note", "source-note"}
    assert any(item["note_type"] == "deep_dive" for item in payload["notes"])
    assert any(
        item["path"] == "20-Areas/AI-Research/Topics/2026-04/Agent Harness_深度解读.md"
        for item in payload["notes"]
    )


def test_truth_api_resolves_deep_dive_source_note_from_frontmatter_and_logs(temp_vault):
    from openclaw_pipeline.truth_api import get_note_provenance

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "2026-04-01_The_Harness_Wars_Begin.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: "The Harness Wars Begin"
source: "https://x.com/0xJsum/status/2039198679815565508"
---

Processed source note.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "2026-04-09_The Harness Wars Begin_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """```yaml
---
title: "The Harness Wars Begin"
source: "https://x.com/0xJsum/status/2039198679815565508"
date: "2026-04-09"
type: "ai"
---
```

# One-liner
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "article_processed",
                        "file": "2026-04-01_The_Harness_Wars_Begin.md",
                        "output": str(deep_dive),
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event_type": "source_archived_to_processed",
                        "source": str(temp_vault / "50-Inbox" / "02-Processing" / "2026-04-01_The_Harness_Wars_Begin.md"),
                        "archived": str(processed),
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    provenance = get_note_provenance(
        temp_vault,
        note_path="20-Areas/AI-Research/Topics/2026-04/2026-04-09_The Harness Wars Begin_深度解读.md",
    )

    assert provenance["original_source_note"] == {
        "title": "The Harness Wars Begin",
        "path": "50-Inbox/03-Processed/2026-04/2026-04-01_The_Harness_Wars_Begin.md",
    }


def test_truth_api_resolves_processed_note_to_derived_deep_dives(temp_vault):
    from openclaw_pipeline.truth_api import get_note_provenance

    processed = temp_vault / "50-Inbox" / "03-Processed" / "2026-04" / "2026-04-01_The_Harness_Wars_Begin.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: "The Harness Wars Begin"
source: "https://x.com/0xJsum/status/2039198679815565508"
---

Processed source note.
""",
        encoding="utf-8",
    )
    deep_dive = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "2026-04-09_The Harness Wars Begin_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """```yaml
---
title: "The Harness Wars Begin"
source: "https://x.com/0xJsum/status/2039198679815565508"
date: "2026-04-09"
type: "ai"
---
```

# One-liner
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        json.dumps(
            {
                "event_type": "article_processed",
                "file": "2026-04-01_The_Harness_Wars_Begin.md",
                "output": str(deep_dive),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    provenance = get_note_provenance(
        temp_vault,
        note_path="50-Inbox/03-Processed/2026-04/2026-04-01_The_Harness_Wars_Begin.md",
    )

    assert provenance["derived_deep_dives"] == [
        {
            "title": "The Harness Wars Begin",
            "path": "20-Areas/AI-Research/Topics/2026-04/2026-04-09_The Harness Wars Begin_深度解读.md",
        }
    ]


def test_truth_api_returns_object_provenance_and_moc_membership(temp_vault):
    from openclaw_pipeline.truth_api import get_object_detail

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

    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    evergreen.write_text(
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

    detail = get_object_detail(temp_vault, "alpha")

    assert detail["object"]["canonical_path"] == "10-Knowledge/Evergreen/Alpha.md"
    assert detail["provenance"]["evergreen_path"] == "10-Knowledge/Evergreen/Alpha.md"
    assert detail["provenance"]["source_notes"][0]["slug"] == "source-deep-dive"
    assert detail["provenance"]["source_notes"][0]["note_type"] == "deep_dive"
    assert detail["provenance"]["mocs"][0]["slug"] == "atlas-index"
    assert detail["provenance"]["mocs"][0]["title"] == "Atlas Index"


def test_truth_api_uses_page_links_for_provenance_resolution(temp_vault):
    from openclaw_pipeline.truth_api import get_object_detail

    source = temp_vault / "20-Areas" / "Tools" / "Topics" / "2026-04" / "Alias Deep Dive_深度解读.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
note_id: alias-deep-dive
title: Alias Deep Dive
type: deep_dive
date: 2026-04-13
---

# Alias Deep Dive

Mentions [[Alpha Concept]].
""",
        encoding="utf-8",
    )
    evergreen = temp_vault / "10-Knowledge" / "Evergreen" / "Alpha.md"
    evergreen.write_text(
        """---
note_id: alpha
title: Alpha
type: evergreen
date: 2026-04-13
aliases: [Alpha Concept]
---

# Alpha

Alpha supports local-first execution.
""",
        encoding="utf-8",
    )
    atlas = temp_vault / "10-Knowledge" / "Atlas" / "Alias Atlas.md"
    atlas.write_text(
        """---
note_id: alias-atlas
title: Alias Atlas
type: moc
date: 2026-04-13
---

# Alias Atlas

- [[Alpha Concept]]
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    detail = get_object_detail(temp_vault, "alpha")

    assert [item["slug"] for item in detail["provenance"]["source_notes"]] == ["alias-deep-dive"]
    assert [item["slug"] for item in detail["provenance"]["mocs"]] == ["alias-atlas"]


def test_truth_api_returns_note_traceability_for_processed_source(temp_vault):
    from openclaw_pipeline.truth_api import get_note_traceability

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
                json.dumps(
                    {
                        "event_type": "article_processed",
                        "file": "Harness.md",
                        "output": str(deep_dive),
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event_type": "evergreen_auto_promoted",
                        "concept": "alpha",
                        "source": "Harness_深度解读.md",
                        "mutation": {"target_slug": "alpha"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    traceability = get_note_traceability(
        temp_vault,
        note_path="50-Inbox/03-Processed/2026-04/Harness.md",
    )

    assert traceability["note"]["path"] == "50-Inbox/03-Processed/2026-04/Harness.md"
    assert [item["title"] for item in traceability["deep_dives"]] == ["Harness Deep Dive"]
    assert [item["object_id"] for item in traceability["objects"]] == ["alpha"]
    assert [item["slug"] for item in traceability["atlas_pages"]] == ["atlas-index"]


def test_truth_api_returns_object_traceability(temp_vault):
    from openclaw_pipeline.truth_api import get_object_traceability

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
        json.dumps(
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "alpha",
                "source": "Harness_深度解读.md",
                "mutation": {"target_slug": "alpha"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    traceability = get_object_traceability(temp_vault, "alpha")

    assert traceability["object"]["object_id"] == "alpha"
    assert [item["slug"] for item in traceability["deep_dives"]] == ["harness-deep-dive"]
    assert [item["path"] for item in traceability["source_notes"]] == ["50-Inbox/03-Processed/2026-04/Harness.md"]
    assert [item["slug"] for item in traceability["atlas_pages"]] == ["atlas-index"]


def test_truth_api_object_traceability_excludes_incidental_deep_dive_mentions(temp_vault):
    from openclaw_pipeline.truth_api import get_object_traceability

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
    promoted = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Harness_深度解读.md"
    promoted.parent.mkdir(parents=True, exist_ok=True)
    promoted.write_text(
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
    incidental = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Incidental_深度解读.md"
    incidental.write_text(
        """---
note_id: incidental-deep-dive
title: Incidental Deep Dive
type: deep_dive
date: 2026-04-13
---

# Incidental Deep Dive

Also mentions [[alpha]] but did not promote it.
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
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        json.dumps(
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "alpha",
                "source": "Harness_深度解读.md",
                "mutation": {"target_slug": "alpha"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    traceability = get_object_traceability(temp_vault, "alpha")

    assert [item["slug"] for item in traceability["deep_dives"]] == ["harness-deep-dive"]


def test_truth_api_returns_note_traceability_for_evergreen_note(temp_vault):
    from openclaw_pipeline.truth_api import get_note_traceability

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
        json.dumps(
            {
                "event_type": "evergreen_auto_promoted",
                "concept": "alpha",
                "source": "Harness_深度解读.md",
                "mutation": {"target_slug": "alpha"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    traceability = get_note_traceability(
        temp_vault,
        note_path="10-Knowledge/Evergreen/Alpha.md",
    )

    assert traceability["note"]["note_type"] == "evergreen"
    assert [item["slug"] for item in traceability["deep_dives"]] == ["harness-deep-dive"]
    assert [item["path"] for item in traceability["source_notes"]] == ["50-Inbox/03-Processed/2026-04/Harness.md"]
    assert [item["object_id"] for item in traceability["objects"]] == ["alpha"]
    assert [item["slug"] for item in traceability["atlas_pages"]] == ["atlas-index"]


def test_truth_api_limits_atlas_memberships_by_page_not_join_rows(temp_vault):
    from openclaw_pipeline.truth_api import list_atlas_memberships

    for note_id, title in (("alpha", "Alpha"), ("beta", "Beta"), ("gamma", "Gamma")):
        note = temp_vault / "10-Knowledge" / "Evergreen" / f"{title}.md"
        note.write_text(
            f"""---
note_id: {note_id}
title: {title}
type: evergreen
date: 2026-04-13
---

# {title}
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
- [[beta]]
- [[gamma]]
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    items = list_atlas_memberships(temp_vault, limit=1)

    assert len(items) == 1
    assert items[0]["slug"] == "atlas-index"
    assert [member["object_id"] for member in items[0]["members"]] == ["alpha", "beta", "gamma"]


def test_truth_api_limits_deep_dive_derivations_by_page_not_join_rows(temp_vault):
    from openclaw_pipeline.truth_api import list_deep_dive_derivations

    for note_id, title in (("alpha", "Alpha"), ("beta", "Beta"), ("gamma", "Gamma")):
        note = temp_vault / "10-Knowledge" / "Evergreen" / f"{title}.md"
        note.write_text(
            f"""---
note_id: {note_id}
title: {title}
type: evergreen
date: 2026-04-13
---

# {title}
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
""",
        encoding="utf-8",
    )
    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "pipeline.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "evergreen_auto_promoted",
                        "concept": "alpha",
                        "source": "Deep Dive_深度解读.md",
                        "mutation": {"target_slug": "alpha"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event_type": "evergreen_auto_promoted",
                        "concept": "beta",
                        "source": "Deep Dive_深度解读.md",
                        "mutation": {"target_slug": "beta"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event_type": "evergreen_auto_promoted",
                        "concept": "gamma",
                        "source": "Deep Dive_深度解读.md",
                        "mutation": {"target_slug": "gamma"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)

    items = list_deep_dive_derivations(temp_vault, limit=1)

    assert len(items) == 1
    assert items[0]["slug"] == "deep-dive"
    assert [member["object_id"] for member in items[0]["derived_objects"]] == [
        "alpha",
        "beta",
        "gamma",
    ]


def test_surface_page_query_clauses_parameterizes_note_type():
    from openclaw_pipeline.truth_api import _surface_page_query_clauses

    where_sql, params = _surface_page_query_clauses(
        note_type="moc",
        normalized_query="alpha",
    )

    assert "pages_index.note_type = ?" in where_sql
    assert params[0] == "moc"


def test_truth_api_syncs_and_lists_active_signals(temp_vault):
    from openclaw_pipeline.truth_api import list_signals, sync_signal_ledger

    vault = _seed_truth_vault(temp_vault)
    thin = vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    thin.write_text(
        """---
note_id: thin-note
title: Thin Note
type: evergreen
date: 2026-04-13
---

# Thin Note

Thin.
""",
        encoding="utf-8",
    )
    loose_source = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    loose_source.parent.mkdir(parents=True, exist_ok=True)
    loose_source.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note with no downstream chain.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    summary = sync_signal_ledger(vault)
    items = list_signals(vault)

    assert summary["signal_count"] >= 3
    assert any(item["signal_type"] == "contradiction_open" for item in items)
    assert any(item["signal_type"] == "stale_summary" for item in items)
    assert any(item["signal_type"] == "production_gap" for item in items)
    contradiction = next(item for item in items if item["signal_type"] == "contradiction_open")
    assert contradiction["source_path"] == "/contradictions"
    assert contradiction["downstream_effects"]
    assert contradiction["recommended_action"]["executable"] is True
    stale = next(item for item in items if item["signal_type"] == "stale_summary")
    assert stale["object_ids"] == ["thin-note"]
    assert stale["source_path"] == "/summaries?q=thin-note"
    assert stale["recommended_action"]["executable"] is True


def test_truth_api_filters_signal_ledger_by_type_and_query(temp_vault):
    from openclaw_pipeline.truth_api import list_signals, sync_signal_ledger

    vault = _seed_truth_vault(temp_vault)
    loose_source = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Loose Source.md"
    loose_source.parent.mkdir(parents=True, exist_ok=True)
    loose_source.write_text(
        """---
title: Loose Source
source: https://example.com/loose
---

Processed source note with no downstream chain.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    sync_signal_ledger(vault)

    production_only = list_signals(vault, signal_type="production_gap")
    searched = list_signals(vault, query="agent harness")

    assert production_only
    assert {item["signal_type"] for item in production_only} == {"production_gap"}
    assert searched
    assert all("agent harness" in f"{item['title']} {item['detail']}".lower() for item in searched)


def test_truth_api_reuses_signal_ledger_when_dependencies_unchanged(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    truth_api.sync_signal_ledger(vault)

    calls = 0
    original = truth_api._compute_signal_entries

    def counted(vault_dir):
        nonlocal calls
        calls += 1
        return original(vault_dir)

    monkeypatch.setattr(truth_api, "_compute_signal_entries", counted)

    first = truth_api.list_signals(vault)
    second = truth_api.list_signals(vault)

    assert first == second
    assert calls == 0


def test_truth_api_includes_extraction_trigger_signals(temp_vault):
    from openclaw_pipeline.truth_api import list_signals, sync_signal_ledger

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    deep_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Harness Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: harness-deep-dive
title: Harness Deep Dive
type: deep_dive
source: https://example.com/another-harness
date: 2026-04-13
---

# Harness Deep Dive

Mentions [[source-note]] but has not produced any evergreen objects yet.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    sync_signal_ledger(vault)
    items = list_signals(vault)

    assert any(item["signal_type"] == "source_needs_deep_dive" for item in items)
    assert any(item["signal_type"] == "deep_dive_needs_objects" for item in items)
    source_signal = next(item for item in items if item["signal_type"] == "source_needs_deep_dive")
    deep_dive_signal = next(item for item in items if item["signal_type"] == "deep_dive_needs_objects")
    assert source_signal["note_paths"] == ["50-Inbox/03-Processed/2026-04/Harness Source.md"]
    assert deep_dive_signal["note_paths"] == ["20-Areas/AI-Research/Topics/2026-04/Harness Deep Dive_深度解读.md"]
    assert source_signal["recommended_action"]["kind"] == "deep_dive_workflow"
    assert source_signal["recommended_action"]["label"] == "Create deep dive"
    assert source_signal["recommended_action"]["executable"] is False
    assert source_signal["recommended_action"]["queue_status"] == "queued"
    assert source_signal["recommended_action"]["action_id"]
    assert deep_dive_signal["recommended_action"]["kind"] == "object_extraction_workflow"
    assert deep_dive_signal["recommended_action"]["label"] == "Extract evergreen objects"
    assert deep_dive_signal["recommended_action"]["executable"] is False
    assert deep_dive_signal["recommended_action"]["queue_status"] == "queued"
    assert deep_dive_signal["recommended_action"]["action_id"]


def test_truth_api_backfills_active_auto_queue_signals_without_duplicates(temp_vault):
    from openclaw_pipeline.truth_api import list_action_queue, list_signals, sync_signal_ledger

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    deep_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Harness Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: harness-deep-dive
title: Harness Deep Dive
type: deep_dive
source: https://example.com/another-harness
date: 2026-04-13
---

# Harness Deep Dive

Mentions [[source-note]] but has not produced any evergreen objects yet.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)

    sync_signal_ledger(vault)
    first_actions = list_action_queue(vault)
    sync_signal_ledger(vault)
    second_actions = list_action_queue(vault)
    items = list_signals(vault)

    assert len(first_actions) == 2
    assert len(second_actions) == 2
    assert {item["action_kind"] for item in second_actions} == {
        "deep_dive_workflow",
        "object_extraction_workflow",
    }
    assert {item["status"] for item in second_actions} == {"queued"}
    assert not any(item["action_kind"] == "review_contradiction" for item in second_actions)
    assert all(
        item["recommended_action"].get("queue_status") == "queued"
        for item in items
        if item["signal_type"] in {"source_needs_deep_dive", "deep_dive_needs_objects"}
    )


def test_truth_api_run_next_action_queue_item_executes_deep_dive_workflow(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    truth_api.sync_signal_ledger(vault)

    calls: list[str] = []

    def fake_run_deep_dive(vault_dir, action):
        calls.append(action["action_kind"])
        return {"output_path": "20-Areas/AI-Research/Topics/2026-04/Harness Source_深度解读.md"}

    monkeypatch.setattr(
        truth_api,
        "_run_deep_dive_workflow_action",
        lambda vault_dir, action: (_ for _ in ()).throw(AssertionError("direct action dispatch")),
    )
    monkeypatch.setattr(
        truth_api,
        "execute_focused_action_handler",
        lambda vault_dir, action, **kwargs: (object(), fake_run_deep_dive(vault_dir, action)),
        raising=False,
    )
    monkeypatch.setattr(truth_api, "_refresh_truth_after_action", lambda vault_dir, **kwargs: None)

    payload = truth_api.run_next_action_queue_item(vault)
    actions = truth_api.list_action_queue(vault)

    assert payload["ran"] is True
    assert payload["action"]["status"] == "succeeded"
    assert payload["action"]["finished_at"]
    assert calls == ["deep_dive_workflow"]
    assert actions[0]["status"] == "succeeded"


def test_truth_api_run_next_action_queue_item_marks_obsolete_when_signal_is_gone(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    truth_api.sync_signal_ledger(vault)

    monkeypatch.setattr(truth_api, "_signal_by_id", lambda vault_dir, signal_id, **kwargs: None)

    payload = truth_api.run_next_action_queue_item(vault)
    actions = truth_api.list_action_queue(vault)

    assert payload["ran"] is False
    assert payload["reason"] == "obsolete_signal"
    assert payload["action"]["status"] == "obsolete"
    assert actions[0]["status"] == "obsolete"


def test_truth_api_can_retry_failed_action_queue_item(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    truth_api.sync_signal_ledger(vault)

    monkeypatch.setattr(
        truth_api,
        "_run_deep_dive_workflow_action",
        lambda vault_dir, action: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    truth_api.run_next_action_queue_item(vault)
    failed_action = truth_api.list_action_queue(vault)[0]

    payload = truth_api.retry_action_queue_item(vault, action_id=failed_action["action_id"])
    retried_action = truth_api.list_action_queue(vault)[0]

    assert payload["retried"] is True
    assert payload["action"]["status"] == "queued"
    assert payload["action"]["error"] == ""
    assert payload["action"]["failure_bucket"] == ""
    assert payload["action"]["started_at"] == ""
    assert payload["action"]["finished_at"] == ""
    assert retried_action["status"] == "queued"


def test_truth_api_can_dismiss_queued_action_queue_item(temp_vault):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    truth_api.sync_signal_ledger(vault)
    queued_action = truth_api.list_action_queue(vault)[0]

    payload = truth_api.dismiss_action_queue_item(vault, action_id=queued_action["action_id"])
    dismissed_action = truth_api.list_action_queue(vault)[0]

    assert payload["dismissed"] is True
    assert payload["action"]["status"] == "dismissed"
    assert payload["action"]["finished_at"]
    assert dismissed_action["status"] == "dismissed"


def test_truth_api_cannot_dismiss_running_action_queue_item(temp_vault):
    import pytest
    import openclaw_pipeline.truth_api as truth_api

    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "actions.jsonl").write_text(
        json.dumps(
            {
                "action_id": "action::running",
                "action_kind": "deep_dive_workflow",
                "source_signal_id": "signal::running",
                "title": "Running action",
                "target_ref": "50-Inbox/03-Processed/Harness.md",
                "status": "running",
                "created_at": "2026-04-15T00:00:01Z",
                "started_at": "2026-04-15T00:00:02Z",
                "retry_count": 0,
                "failure_bucket": "",
                "safe_to_run": True,
                "payload": {},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="action is not dismissible"):
        truth_api.dismiss_action_queue_item(temp_vault, action_id="action::running")


def test_truth_api_run_action_queue_processes_multiple_queued_items(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    deep_dive = vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "Harness Deep Dive_深度解读.md"
    deep_dive.parent.mkdir(parents=True, exist_ok=True)
    deep_dive.write_text(
        """---
note_id: harness-deep-dive
title: Harness Deep Dive
type: deep_dive
source: https://example.com/another-harness
date: 2026-04-13
---

# Harness Deep Dive

Mentions [[source-note]] but has not produced any evergreen objects yet.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    truth_api.sync_signal_ledger(vault)

    monkeypatch.setattr(truth_api, "_run_deep_dive_workflow_action", lambda vault_dir, action: {"ok": "deep_dive"})
    monkeypatch.setattr(truth_api, "_run_object_extraction_workflow_action", lambda vault_dir, action: {"ok": "objects"})
    monkeypatch.setattr(truth_api, "_refresh_truth_after_action", lambda vault_dir, **kwargs: None)

    payload = truth_api.run_action_queue(vault, limit=5)
    actions = truth_api.list_action_queue(vault)

    assert payload["ran_count"] == 2
    assert payload["safe_only"] is False
    assert payload["stopped_reason"] == "no_queued_actions"
    assert {item["status"] for item in actions} == {"succeeded"}


def test_truth_api_failed_action_tracks_retry_count_and_failure_bucket(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    truth_api.sync_signal_ledger(vault)

    monkeypatch.setattr(
        truth_api,
        "_run_deep_dive_workflow_action",
        lambda vault_dir, action: (_ for _ in ()).throw(FileNotFoundError("source note not found")),
    )

    payload = truth_api.run_next_action_queue_item(vault, safe_only=True)
    failed_action = truth_api.list_action_queue(vault)[0]

    assert payload["ran"] is False
    assert payload["safe_only"] is True
    assert failed_action["status"] == "failed"
    assert failed_action["retry_count"] == 1
    assert failed_action["failure_bucket"] == "missing_target"


def test_truth_api_run_action_queue_can_limit_to_safe_actions(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "actions.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "action_id": "action::manual",
                        "action_kind": "review_contradiction",
                        "source_signal_id": "signal::manual",
                        "title": "Manual review",
                        "target_ref": "/contradictions",
                        "status": "queued",
                        "created_at": "2026-04-15T00:00:00Z",
                        "retry_count": 0,
                        "failure_bucket": "",
                        "safe_to_run": False,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "action_id": "action::safe",
                        "action_kind": "deep_dive_workflow",
                        "source_signal_id": "signal::safe",
                        "title": "Safe action",
                        "target_ref": "50-Inbox/03-Processed/Harness.md",
                        "status": "queued",
                        "created_at": "2026-04-15T00:00:01Z",
                        "retry_count": 0,
                        "failure_bucket": "",
                        "safe_to_run": True,
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        truth_api,
        "_signal_by_id",
        lambda vault_dir, signal_id, **kwargs: {"signal_id": signal_id},
    )
    monkeypatch.setattr(truth_api, "_run_deep_dive_workflow_action", lambda vault_dir, action: {"ok": True})
    monkeypatch.setattr(truth_api, "_refresh_truth_after_action", lambda vault_dir, **kwargs: None)

    payload = truth_api.run_action_queue(temp_vault, limit=1, safe_only=True)
    actions = truth_api.list_action_queue(temp_vault)
    safe = next(item for item in actions if item["action_id"] == "action::safe")
    manual = next(item for item in actions if item["action_id"] == "action::manual")

    assert payload["safe_only"] is True
    assert payload["ran_count"] == 1
    assert safe["status"] == "succeeded"
    assert manual["status"] == "queued"


def test_truth_api_builds_briefing_snapshot(temp_vault):
    from openclaw_pipeline.truth_api import get_briefing_snapshot, record_review_action, sync_signal_ledger

    vault = _seed_truth_vault(temp_vault)
    thin = vault / "10-Knowledge" / "Evergreen" / "Thin.md"
    thin.write_text(
        """---
note_id: thin-note
title: Thin Note
type: evergreen
date: 2026-04-13
---

# Thin Note

Thin.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    record_review_action(
        vault,
        event_type="ui_summaries_rebuilt",
        slug="source-note",
        payload={
            "object_ids": ["source-note"],
            "objects_rebuilt": 1,
            "rebuilt_object_ids": ["source-note"],
        },
    )
    sync_signal_ledger(vault)

    payload = get_briefing_snapshot(vault)

    assert payload["recent_signal_count"] >= 1
    assert payload["unresolved_issue_count"] >= 1
    assert payload["recent_signals"]
    assert payload["unresolved_issues"]
    assert any(item["object_id"] == "source-note" for item in payload["changed_objects"])
    assert payload["active_topics"]
    assert payload["insights"]
    assert payload["priority_items"]
    assert payload["first_useful_sign"] in payload["insights"]
    assert any(item.get("recommended_action") for item in payload["priority_items"])


def test_truth_api_briefing_dedupes_equivalent_evolution_insights(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    monkeypatch.setattr(truth_api, "_list_signals_from_ledger", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        truth_api,
        "list_evolution_candidates",
        lambda vault_dir, limit=24: [
            {
                "link_type": "challenges",
                "subject_id": "agent harness",
                "object_ids": ["source-note"],
                "source_paths": ["10-Knowledge/Evergreen/Source.md"],
            },
            {
                "link_type": "challenges",
                "subject_id": "agent harness",
                "object_ids": ["source-note"],
                "source_paths": ["10-Knowledge/Evergreen/Source.md"],
            },
        ],
    )

    payload = truth_api._research_tech_build_briefing_snapshot(vault, limit=8)

    assert len(payload["insights"]) == 1
    assert len(payload["priority_items"]) == 1


def test_truth_api_briefing_prioritizes_actionable_unresolved_issues(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    monkeypatch.setattr(
        truth_api,
        "_list_signals_from_ledger",
        lambda *args, **kwargs: [
            {
                "signal_id": "signal::source",
                "signal_type": "source_needs_deep_dive",
                "title": "Manual extraction gap",
                "detail": "Needs deep dive.",
                "source_path": "/note?path=50-Inbox/03-Processed/2026-04/Manual.md",
                "note_paths": ["50-Inbox/03-Processed/2026-04/Manual.md"],
                "object_ids": [],
                "recommended_action": {
                    "kind": "deep_dive_workflow",
                    "label": "Create deep dive",
                    "path": "/note?path=50-Inbox/03-Processed/2026-04/Manual.md",
                    "executable": False,
                },
            },
            {
                "signal_id": "signal::contradiction",
                "signal_type": "contradiction_open",
                "title": "Agent harness contradiction",
                "detail": "Open contradiction.",
                "source_path": "/contradictions?q=agent%20harness",
                "note_paths": [],
                "object_ids": ["source-note"],
                "recommended_action": {
                    "kind": "review_contradiction",
                    "label": "Review contradiction",
                    "path": "/contradictions?q=agent%20harness",
                    "executable": True,
                },
            },
        ],
    )
    monkeypatch.setattr(truth_api, "list_evolution_candidates", lambda vault_dir, limit=24: [])

    payload = truth_api._research_tech_build_briefing_snapshot(vault, limit=8)

    assert payload["priority_items"][0]["kind"] == "contradiction_open"


def test_truth_api_enqueues_signal_actions_idempotently(temp_vault):
    from openclaw_pipeline.truth_api import enqueue_signal_action, list_action_queue, list_signals, sync_signal_ledger

    vault = _seed_truth_vault(temp_vault)
    processed = vault / "50-Inbox" / "03-Processed" / "2026-04" / "Harness Source.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(
        """---
title: Harness Source
source: https://example.com/harness
---

Processed source note without any derived deep dive.
""",
        encoding="utf-8",
    )
    rebuild_knowledge_index(vault)
    sync_signal_ledger(vault)
    source_signal = next(item for item in list_signals(vault) if item["signal_type"] == "source_needs_deep_dive")

    first = enqueue_signal_action(vault, signal_id=source_signal["signal_id"])
    second = enqueue_signal_action(vault, signal_id=source_signal["signal_id"])
    actions = list_action_queue(vault)
    refreshed_signal = next(item for item in list_signals(vault) if item["signal_id"] == source_signal["signal_id"])

    assert first["created"] is False
    assert second["created"] is False
    assert first["action"]["action_id"] == second["action"]["action_id"]
    assert len(actions) == 1
    assert actions[0]["status"] == "queued"
    assert refreshed_signal["recommended_action"]["queue_status"] == "queued"
    assert refreshed_signal["recommended_action"]["action_id"] == actions[0]["action_id"]


def test_truth_api_enqueue_signal_action_uses_action_queue_lock(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    vault = _seed_truth_vault(temp_vault)
    rebuild_knowledge_index(vault)
    truth_api.sync_signal_ledger(vault)
    contradiction_signal = next(
        item for item in truth_api.list_signals(vault) if item["signal_type"] == "contradiction_open"
    )
    calls: list[str] = []

    @contextmanager
    def fake_lock(vault_dir, *, timeout_seconds=300.0):
        calls.append(f"enter:{vault_dir == vault}")
        yield
        calls.append("exit")

    monkeypatch.setattr(truth_api, "action_queue_write_lock", fake_lock, raising=False)

    payload = truth_api.enqueue_signal_action(vault, signal_id=contradiction_signal["signal_id"])

    assert payload["created"] is True
    assert calls == [f"enter:{True}", "exit"]


def test_truth_api_includes_review_action_signals(temp_vault):
    from openclaw_pipeline.truth_api import list_signals, record_review_action, sync_signal_ledger

    vault = _seed_truth_vault(temp_vault)
    record_review_action(
        vault,
        event_type="ui_contradictions_resolved",
        slug="source-note",
        payload={
            "object_ids": ["source-note"],
            "contradiction_ids": ["contradiction::alpha"],
            "status": "dismissed",
            "note": "Reviewed",
            "rebuilt_object_ids": ["source-note"],
        },
    )
    record_review_action(
        vault,
        event_type="ui_summaries_rebuilt",
        slug="source-note",
        payload={
            "object_ids": ["source-note"],
            "objects_rebuilt": 1,
            "rebuilt_object_ids": ["source-note"],
        },
    )

    sync_signal_ledger(vault)
    items = list_signals(vault)

    assert any(item["signal_type"] == "contradiction_reviewed" for item in items)
    assert any(item["signal_type"] == "summary_rebuilt" for item in items)
    reviewed = next(item for item in items if item["signal_type"] == "contradiction_reviewed")
    assert reviewed["object_ids"] == ["source-note"]
    assert reviewed["source_path"] == "/contradictions?status=resolved"


def test_truth_api_sync_signal_ledger_writes_jsonl_without_db_lock(temp_vault, monkeypatch):
    from openclaw_pipeline import truth_api

    vault = _seed_truth_vault(temp_vault)
    calls: list[str] = []

    @contextmanager
    def fake_lock(vault_dir, *, timeout_seconds=300.0):
        calls.append(f"enter:{vault_dir == vault}")
        yield
        calls.append("exit")

    monkeypatch.setattr(truth_api, "knowledge_db_write_lock", fake_lock)

    summary = truth_api.sync_signal_ledger(vault)

    assert calls == []
    signals_path = vault / "60-Logs" / "signals.jsonl"
    assert signals_path.exists()
    assert summary["signal_count"] >= 1


def test_truth_api_sync_signal_ledger_uses_signal_ledger_lock(temp_vault, monkeypatch):
    from openclaw_pipeline import truth_api

    vault = _seed_truth_vault(temp_vault)
    calls: list[str] = []

    @contextmanager
    def fake_lock(vault_dir, *, timeout_seconds=300.0):
        calls.append(f"enter:{vault_dir == vault}")
        yield
        calls.append("exit")

    monkeypatch.setattr(truth_api, "signal_ledger_write_lock", fake_lock, raising=False)
    monkeypatch.setattr(
        truth_api,
        "_backfill_auto_queue_actions",
        lambda vault_dir, *, pack_name=None, signals=None: {"created_count": 0, "created_action_ids": []},
    )

    truth_api.sync_signal_ledger(vault)

    assert calls == [f"enter:{True}", "exit"]


def test_truth_api_run_next_action_queue_item_uses_action_queue_lock(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    logs_dir = temp_vault / "60-Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "actions.jsonl").write_text(
        json.dumps(
            {
                "action_id": "action::safe",
                "action_kind": "deep_dive_workflow",
                "source_signal_id": "signal::safe",
                "title": "Safe action",
                "target_ref": "50-Inbox/03-Processed/Harness.md",
                "status": "queued",
                "created_at": "2026-04-15T00:00:01Z",
                "retry_count": 0,
                "failure_bucket": "",
                "safe_to_run": True,
                "payload": {},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    @contextmanager
    def fake_lock(vault_dir, *, timeout_seconds=300.0):
        calls.append(f"enter:{vault_dir == temp_vault}")
        yield
        calls.append("exit")

    monkeypatch.setattr(truth_api, "action_queue_write_lock", fake_lock, raising=False)
    monkeypatch.setattr(
        truth_api,
        "_signal_by_id",
        lambda vault_dir, signal_id, **kwargs: {"signal_id": signal_id},
    )
    monkeypatch.setattr(truth_api, "_run_deep_dive_workflow_action", lambda vault_dir, action: {"ok": True})
    monkeypatch.setattr(truth_api, "_refresh_truth_after_action", lambda vault_dir, **kwargs: None)

    payload = truth_api.run_next_action_queue_item(temp_vault, safe_only=True)

    assert payload["ran"] is True
    assert calls == [f"enter:{True}", "exit", f"enter:{True}", "exit"]


def test_truth_api_refresh_truth_after_action_uses_pack_override(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api_source

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "openclaw_pipeline.knowledge_index.rebuild_knowledge_index",
        lambda vault_dir, *, pack_name=None: captured.update(
            {"vault_dir": vault_dir, "pack_name": pack_name}
        ),
    )
    monkeypatch.setattr(
        truth_api_source,
        "sync_signal_ledger",
        lambda vault_dir, **kwargs: captured.update({"synced": vault_dir, "synced_pack": kwargs.get("pack_name")}),
    )

    truth_api_source._refresh_truth_after_action(
        temp_vault,
        pack_name="default-knowledge",
        requires_truth_refresh=True,
        requires_signal_resync=True,
    )

    assert captured["pack_name"] == "default-knowledge"
    assert captured["synced"] == temp_vault
    assert captured["synced_pack"] == "default-knowledge"


def test_truth_api_refresh_truth_after_action_respects_independent_flags(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api_source

    calls: list[str] = []

    monkeypatch.setattr(
        "openclaw_pipeline.knowledge_index.rebuild_knowledge_index",
        lambda vault_dir, *, pack_name=None: calls.append(f"rebuild:{pack_name}"),
    )
    monkeypatch.setattr(
        truth_api_source,
        "sync_signal_ledger",
        lambda vault_dir, **kwargs: calls.append(f"sync:{kwargs.get('pack_name')}"),
    )

    truth_api_source._refresh_truth_after_action(
        temp_vault,
        pack_name="default-knowledge",
        requires_truth_refresh=False,
        requires_signal_resync=True,
    )

    assert calls == ["sync:default-knowledge"]


def test_action_id_includes_pack_to_avoid_cross_pack_collisions():
    from openclaw_pipeline.truth_api import _action_id

    payload = {"recommended_action": {"kind": "deep_dive_workflow"}}

    research = _action_id(
        "signal::demo",
        "deep_dive_workflow",
        "50-Inbox/03-Processed/Demo.md",
        payload,
        pack_name="research-tech",
    )
    media = _action_id(
        "signal::demo",
        "deep_dive_workflow",
        "50-Inbox/03-Processed/Demo.md",
        payload,
        pack_name="media-editorial",
    )

    assert research != media


def test_truth_api_list_signals_dispatches_via_observation_surface_registry(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api_source

    calls: list[tuple[str, str | None]] = []

    def fake_execute(*, surface_kind, vault_dir, pack_name=None, **kwargs):
        calls.append((surface_kind, pack_name))
        if surface_kind == "signals":
            return object(), [
                {
                    "signal_id": "signal::1",
                    "signal_type": "production_gap",
                    "title": "Gap",
                    "detail": "detail",
                    "recommended_action": {"label": "Inspect"},
                }
            ]
        raise AssertionError(f"unexpected surface {surface_kind}")

    monkeypatch.setattr(
        truth_api_source,
        "execute_observation_surface_builder",
        fake_execute,
        raising=False,
    )
    monkeypatch.setattr(
        truth_api_source,
        "_rewrite_jsonl",
        lambda path, items: path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + ("\n" if items else ""),
            encoding="utf-8",
        ),
    )

    items = truth_api_source.list_signals(temp_vault, pack_name="default-knowledge")

    assert items[0]["signal_id"] == "signal::1"
    assert calls == [("signals", "default-knowledge")]


def test_research_tech_build_signal_entries_uses_pack_aware_production_chains(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api_source

    calls: list[str | None] = []

    monkeypatch.setattr(truth_api_source, "list_contradictions", lambda *args, **kwargs: [])
    monkeypatch.setattr(truth_api_source, "list_stale_summaries", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        truth_api_source,
        "list_production_chains",
        lambda vault_dir, *, pack_name=None, **kwargs: calls.append(pack_name) or [],
    )

    items = truth_api_source._research_tech_build_signal_entries(
        temp_vault,
        pack_name="default-knowledge",
    )

    assert items == []
    assert calls == ["default-knowledge"]


def test_truth_api_get_briefing_snapshot_dispatches_via_observation_surface_registry(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api_source

    def fake_execute(*, surface_kind, vault_dir, pack_name=None, **kwargs):
        if surface_kind == "signals":
            return object(), []
        if surface_kind == "briefing":
            return object(), {"generated_at": "2026-04-15T00:00:00Z", "priority_item_count": 1, "recent_signals": []}
        raise AssertionError(f"unexpected surface {surface_kind}")

    monkeypatch.setattr(
        truth_api_source,
        "execute_observation_surface_builder",
        fake_execute,
        raising=False,
    )

    payload = truth_api_source.get_briefing_snapshot(temp_vault, pack_name="default-knowledge", limit=5)

    assert payload["priority_item_count"] == 1


def test_truth_api_list_production_chains_dispatches_via_observation_surface_registry(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api_source

    calls: list[tuple[str, str | None, str | None]] = []

    def fake_execute(*, surface_kind, vault_dir, pack_name=None, **kwargs):
        calls.append((surface_kind, pack_name, kwargs.get("query")))
        return object(), [{"title": "Chain", "path": "20-Areas/AI/Chain.md", "traceability": {}}]

    monkeypatch.setattr(
        truth_api_source,
        "execute_observation_surface_builder",
        fake_execute,
        raising=False,
    )

    items = truth_api_source.list_production_chains(
        temp_vault,
        pack_name="default-knowledge",
        query="chain",
    )

    assert items[0]["title"] == "Chain"
    assert calls == [("production_chains", "default-knowledge", "chain")]


def test_truth_api_compute_signal_entries_reuses_production_chains(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    calls = {"chains": 0}

    monkeypatch.setattr(truth_api, "list_contradictions", lambda *args, **kwargs: [])
    monkeypatch.setattr(truth_api, "list_stale_summaries", lambda *args, **kwargs: [])
    monkeypatch.setattr(truth_api, "list_review_actions", lambda *args, **kwargs: [])

    def fake_chains(vault_dir, *, query=None, limit=100):
        calls["chains"] += 1
        return [
            {
                "title": "Loose Source",
                "path": "50-Inbox/03-Processed/2026-04/Loose Source.md",
                "stage_label": "source_note",
                "traceability": {
                    "deep_dives": [],
                    "objects": [],
                    "atlas_pages": [],
                    "source_notes": [],
                    "counts": {
                        "source_notes": 0,
                        "deep_dives": 0,
                        "objects": 0,
                        "atlas_pages": 0,
                    },
                },
            },
            {
                "title": "Loose Deep Dive",
                "path": "20-Areas/AI-Research/Topics/2026-04/Loose Deep Dive_深度解读.md",
                "stage_label": "deep_dive",
                "traceability": {
                    "deep_dives": [],
                    "objects": [],
                    "atlas_pages": [],
                    "source_notes": [{"path": "50-Inbox/03-Processed/2026-04/Loose Source.md"}],
                    "counts": {
                        "source_notes": 1,
                        "deep_dives": 0,
                        "objects": 0,
                        "atlas_pages": 0,
                    },
                },
            },
        ]

    monkeypatch.setattr(truth_api, "_research_tech_list_production_chains", fake_chains)
    monkeypatch.setattr(
        truth_api,
        "list_production_gaps",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call list_production_gaps")),
    )

    items = truth_api._research_tech_build_signal_entries(temp_vault)

    assert calls["chains"] == 1
    assert {item["signal_type"] for item in items} >= {
        "production_gap",
        "source_needs_deep_dive",
        "deep_dive_needs_objects",
    }


def test_truth_api_briefing_batches_topic_title_lookups(temp_vault, monkeypatch):
    import openclaw_pipeline.truth_api as truth_api

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        truth_api,
        "_list_signals_from_ledger",
        lambda *args, **kwargs: [
            {
                "signal_id": "signal::a",
                "signal_type": "source_needs_deep_dive",
                "title": "Topic A",
                "detail": "Needs work.",
                "source_path": "/note?path=a",
                "note_paths": ["a"],
                "object_ids": ["source-note"],
                "recommended_action": None,
            },
            {
                "signal_id": "signal::b",
                "signal_type": "deep_dive_needs_objects",
                "title": "Topic B",
                "detail": "Needs objects.",
                "source_path": "/note?path=b",
                "note_paths": ["b"],
                "object_ids": ["target-note"],
                "recommended_action": None,
            },
        ],
    )
    monkeypatch.setattr(truth_api, "list_evolution_candidates", lambda vault_dir, limit=24: [])
    monkeypatch.setattr(truth_api, "list_action_queue", lambda vault_dir, limit=100: [])

    def fake_batch(vault_dir, object_ids):
        calls.append(tuple(object_ids))
        return {
            object_id: {"title": f"title:{object_id}"}
            for object_id in object_ids
        }

    monkeypatch.setattr(truth_api, "_batch_object_rows", fake_batch)

    payload = truth_api._research_tech_build_briefing_snapshot(temp_vault, limit=8)

    assert payload["active_topics"] == [
        {
            "object_id": "source-note",
            "title": "title:source-note",
            "signal_count": 1,
            "path": "/topic?id=source-note",
        },
        {
            "object_id": "target-note",
            "title": "title:target-note",
            "signal_count": 1,
            "path": "/topic?id=target-note",
        },
    ]
    non_empty_calls = [set(call) for call in calls if call]
    assert non_empty_calls == [{"source-note", "target-note"}]


def test_truth_api_record_review_action_writes_jsonl_without_db_lock(temp_vault, monkeypatch):
    from openclaw_pipeline import truth_api

    vault = _seed_truth_vault(temp_vault)
    calls: list[str] = []

    @contextmanager
    def fake_lock(vault_dir, *, timeout_seconds=300.0):
        calls.append(f"enter:{vault_dir == vault}")
        yield
        calls.append("exit")

    monkeypatch.setattr(truth_api, "knowledge_db_write_lock", fake_lock)

    truth_api.record_review_action(
        vault,
        event_type="ui_summaries_rebuilt",
        slug="source-note",
        payload={
            "object_ids": ["source-note"],
            "objects_rebuilt": 1,
            "rebuilt_object_ids": ["source-note"],
        },
    )

    assert calls == []
    review_log = vault / "60-Logs" / "review-actions.jsonl"
    assert review_log.exists()
    lines = [line for line in review_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
