from __future__ import annotations

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
