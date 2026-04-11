from __future__ import annotations

import sqlite3


def test_rebuild_knowledge_index_populates_truth_store_tables(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

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

Source note explains the runtime architecture.

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

Target note captures downstream effects.
""",
        encoding="utf-8",
    )

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    assert result["objects_indexed"] == 2
    assert result["claims_indexed"] == 2
    assert result["relations_indexed"] == 1
    assert result["compiled_summaries_indexed"] == 2
    assert result["contradictions_indexed"] == 0

    with sqlite3.connect(db_path) as conn:
        objects = conn.execute(
            "SELECT object_id, object_kind, title FROM objects ORDER BY object_id"
        ).fetchall()
        claims = conn.execute(
            "SELECT object_id, claim_text, claim_kind FROM claims ORDER BY object_id"
        ).fetchall()
        relations = conn.execute(
            "SELECT source_object_id, target_object_id, relation_type FROM relations"
        ).fetchall()
        summaries = conn.execute(
            "SELECT object_id, summary_text FROM compiled_summaries ORDER BY object_id"
        ).fetchall()

    assert objects == [
        ("source-note", "evergreen", "Source Note"),
        ("target-note", "evergreen", "Target Note"),
    ]
    assert claims == [
        ("source-note", "Source note explains the runtime architecture.", "page_summary"),
        ("target-note", "Target note captures downstream effects.", "page_summary"),
    ]
    assert relations == [("source-note", "target-note", "wikilink")]
    assert summaries == [
        ("source-note", "Source note explains the runtime architecture."),
        ("target-note", "Target note captures downstream effects."),
    ]


def test_rebuild_knowledge_index_persists_detected_contradictions(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    one = temp_vault / "10-Knowledge" / "Evergreen" / "One.md"
    two = temp_vault / "10-Knowledge" / "Evergreen" / "Two.md"
    one.write_text(
        """---
note_id: harness-positive
title: Harness Positive
type: evergreen
date: 2026-04-10
---

# Harness Positive

Agent harness supports local-first execution for operators.
""",
        encoding="utf-8",
    )
    two.write_text(
        """---
note_id: harness-negative
title: Harness Negative
type: evergreen
date: 2026-04-10
---

# Harness Negative

Agent harness does not support local-first execution for operators.
""",
        encoding="utf-8",
    )

    result = rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    assert result["contradictions_indexed"] == 1

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT subject_key, positive_claim_ids_json, negative_claim_ids_json, status, resolution_note, resolved_at
            FROM contradictions
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "agent harness"
    assert "harness-positive::" in rows[0][1]
    assert "harness-negative::" in rows[0][2]
    assert rows[0][3] == "open"
    assert rows[0][4] == ""
    assert rows[0][5] == ""


def test_knowledge_index_stats_include_truth_store_counts(temp_vault):
    from openclaw_pipeline.knowledge_index import knowledge_index_stats, rebuild_knowledge_index

    note = temp_vault / "10-Knowledge" / "Evergreen" / "One.md"
    note.write_text(
        """---
note_id: one
title: One
type: evergreen
date: 2026-04-10
---

# One

One note defines a durable concept.
""",
        encoding="utf-8",
    )

    rebuild_knowledge_index(temp_vault)
    stats = knowledge_index_stats(temp_vault)

    assert stats["objects"] == 1
    assert stats["claims"] == 1
    assert stats["relations"] == 0
    assert stats["compiled_summaries"] == 1
    assert stats["contradictions"] == 0
