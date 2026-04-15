from __future__ import annotations

import json
from pathlib import Path

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
    assert source_signal["recommended_action"] == {
        "kind": "deep_dive_workflow",
        "label": "Create deep dive",
        "path": "/note?path=50-Inbox%2F03-Processed%2F2026-04%2FHarness%20Source.md",
        "executable": False,
    }
    assert deep_dive_signal["recommended_action"] == {
        "kind": "object_extraction_workflow",
        "label": "Extract evergreen objects",
        "path": "/note?path=20-Areas%2FAI-Research%2FTopics%2F2026-04%2FHarness%20Deep%20Dive_%E6%B7%B1%E5%BA%A6%E8%A7%A3%E8%AF%BB.md",
        "executable": False,
    }


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

    assert first["created"] is True
    assert second["created"] is False
    assert first["action"]["action_id"] == second["action"]["action_id"]
    assert len(actions) == 1
    assert actions[0]["status"] == "queued"
    assert refreshed_signal["recommended_action"]["queue_status"] == "queued"
    assert refreshed_signal["recommended_action"]["action_id"] == actions[0]["action_id"]


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
