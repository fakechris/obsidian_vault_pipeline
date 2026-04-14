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
    assert payload["context"]["source_slug"] == "alpha"
    assert payload["section_nav"][0]["href"] == "#summary"


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


def test_build_event_dossier_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    _seed_truth_store(temp_vault)

    payload = build_event_dossier_payload(temp_vault)

    assert payload["screen"] == "event/dossier"
    assert payload["event_count"] == 3
    assert payload["dates"] == ["2026-04-13"]
    assert payload["events"][0]["object_id"] == "alpha"
    assert payload["date_sections"][0]["date"] == "2026-04-13"


def test_build_contradiction_browser_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_contradiction_browser_payload

    _seed_truth_store(temp_vault)

    payload = build_contradiction_browser_payload(temp_vault)

    assert payload["screen"] == "truth/contradictions"
    assert payload["count"] == 1
    assert payload["items"][0]["subject_key"] == "alpha"
    assert payload["open_count"] == 1
    assert payload["items"][0]["object_ids"] == ["alpha", "conflict"]


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


def test_build_truth_dashboard_payload(temp_vault):
    from openclaw_pipeline.ui.view_models import build_truth_dashboard_payload

    _seed_truth_store(temp_vault)

    payload = build_truth_dashboard_payload(temp_vault)

    assert payload["screen"] == "truth/dashboard"
    assert payload["objects"]["count"] == 3
    assert payload["contradictions"]["count"] == 1
    assert payload["events"]["count"] == 3
    assert payload["objects"]["items"][0]["object_id"] == "alpha"


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


def test_build_event_dossier_payload_filters_by_query(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    _seed_truth_store(temp_vault)

    payload = build_event_dossier_payload(temp_vault, query="beta")

    assert payload["event_count"] == 1
    assert [item["object_id"] for item in payload["events"]] == ["beta"]


def test_build_event_dossier_payload_applies_limit_before_materializing(temp_vault):
    from openclaw_pipeline.ui.view_models import build_event_dossier_payload

    _seed_truth_store(temp_vault)

    payload = build_event_dossier_payload(temp_vault, limit=2)

    assert payload["event_count"] == 2
    assert len(payload["events"]) == 2


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
