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
