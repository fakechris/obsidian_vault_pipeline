from __future__ import annotations

import json
import sqlite3


def test_rebuild_compiled_summaries_updates_summary_for_object(temp_vault):
    from openclaw_pipeline.knowledge_index import rebuild_compiled_summaries, rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

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
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE compiled_summaries SET summary_text = ? WHERE object_id = ?",
            ("Thin.", "thin-note"),
        )
        conn.commit()

    result = rebuild_compiled_summaries(temp_vault, object_ids=["thin-note"])

    assert result["objects_rebuilt"] == 1

    with sqlite3.connect(db_path) as conn:
        summary = conn.execute(
            "SELECT summary_text FROM compiled_summaries WHERE object_id = ?",
            ("thin-note",),
        ).fetchone()[0]

    assert summary == "Thin note."


def test_rebuild_summaries_command_can_apply_stale_summary_queue(temp_vault, capsys):
    from openclaw_pipeline.commands.rebuild_summaries import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

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
    layout = VaultLayout.from_vault(temp_vault)
    queue_file = layout.review_queue_dir / "stale-summaries" / "thin-note.json"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(
        json.dumps(
            {
                "queue_name": "stale-summaries",
                "issue_type": "stale-compiled-summary",
                "object_id": "thin-note",
                "review_required": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = main(["--vault-dir", str(temp_vault), "--from-queue", "stale-summaries", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["objects_rebuilt"] == 1
    assert payload["object_ids"] == ["thin-note"]
