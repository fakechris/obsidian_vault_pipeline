from __future__ import annotations

import json
import sqlite3


def _build_contradiction(temp_vault):
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
    rebuild_knowledge_index(temp_vault)
    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        contradiction_id = conn.execute(
            "SELECT contradiction_id FROM contradictions"
        ).fetchone()[0]
    return contradiction_id, one, two


def test_resolve_contradictions_command_updates_truth_store_status(temp_vault, capsys):
    from openclaw_pipeline.commands.resolve_contradictions import main
    from openclaw_pipeline.runtime import VaultLayout

    contradiction_id, _one, _two = _build_contradiction(temp_vault)

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--contradiction-id",
            contradiction_id,
            "--status",
            "resolved_keep_positive",
            "--note",
            "Confirmed the positive claim after review.",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_count"] == 1
    assert payload["contradiction_ids"] == [contradiction_id]

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, resolution_note
            FROM contradictions
            WHERE contradiction_id = ?
            """,
            (contradiction_id,),
        ).fetchone()

    assert row == ("resolved_keep_positive", "Confirmed the positive claim after review.")


def test_resolve_contradictions_command_can_apply_review_queue(temp_vault, capsys):
    from openclaw_pipeline.commands.resolve_contradictions import main
    from openclaw_pipeline.operations.runtime import run_operation_profile
    from openclaw_pipeline.packs.loader import load_pack
    from openclaw_pipeline.runtime import VaultLayout

    contradiction_id, _one, _two = _build_contradiction(temp_vault)
    pack = load_pack("default-knowledge")
    profile = pack.operation_profile("truth/contradiction_review")
    written = run_operation_profile(temp_vault, profile)
    assert len(written) == 1
    queue_file = written[0]
    assert queue_file.exists()

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--from-queue",
            "contradictions",
            "--status",
            "dismissed",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_count"] == 1
    assert payload["contradiction_ids"] == [contradiction_id]
    assert payload["cleared_queue_files"] == [str(queue_file)]
    assert not queue_file.exists()

    db_path = VaultLayout.from_vault(temp_vault).knowledge_db
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, resolution_note
            FROM contradictions
            WHERE contradiction_id = ?
            """,
            (contradiction_id,),
        ).fetchone()

    assert row == ("dismissed", "")


def test_resolve_contradictions_command_can_rebuild_affected_summaries(temp_vault, capsys):
    from openclaw_pipeline.commands.resolve_contradictions import main
    from openclaw_pipeline.knowledge_index import rebuild_knowledge_index
    from openclaw_pipeline.runtime import VaultLayout

    contradiction_id, _one, _two = _build_contradiction(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)
    db_path = layout.knowledge_db
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE compiled_summaries
            SET summary_text = 'STALE SUMMARY'
            WHERE object_id IN ('harness-positive', 'harness-negative')
            """
        )
        conn.commit()

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--contradiction-id",
            contradiction_id,
            "--status",
            "resolved_keep_positive",
            "--rebuild-summaries",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_count"] == 1
    assert payload["rebuilt_summary_count"] == 2
    assert payload["rebuilt_object_ids"] == ["harness-negative", "harness-positive"]

    with sqlite3.connect(db_path) as conn:
        summaries = conn.execute(
            """
            SELECT object_id, summary_text
            FROM compiled_summaries
            WHERE object_id IN ('harness-positive', 'harness-negative')
            ORDER BY object_id
            """
        ).fetchall()

    assert summaries == [
        ("harness-negative", "Agent harness does not support local-first execution for operators."),
        ("harness-positive", "Agent harness supports local-first execution for operators."),
    ]


def test_resolve_contradictions_command_only_clears_resolved_queue_files(temp_vault, capsys):
    from openclaw_pipeline.commands.resolve_contradictions import main
    from openclaw_pipeline.runtime import VaultLayout

    contradiction_id, _one, _two = _build_contradiction(temp_vault)
    layout = VaultLayout.from_vault(temp_vault)
    queue_dir = layout.review_queue_dir / "contradictions"
    queue_dir.mkdir(parents=True, exist_ok=True)
    resolved_file = queue_dir / "resolved.json"
    unresolved_file = queue_dir / "unresolved.json"
    resolved_file.write_text(json.dumps({"contradiction_id": contradiction_id}, ensure_ascii=False), encoding="utf-8")
    unresolved_file.write_text(
        json.dumps({"contradiction_id": "contradiction::missing"}, ensure_ascii=False),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--vault-dir",
            str(temp_vault),
            "--from-queue",
            "contradictions",
            "--status",
            "dismissed",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_count"] == 1
    assert payload["cleared_queue_files"] == [str(resolved_file)]
    assert not resolved_file.exists()
    assert unresolved_file.exists()
