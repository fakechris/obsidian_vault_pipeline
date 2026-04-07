from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from openclaw_pipeline.autopilot.queue import Task, TaskQueue
from openclaw_pipeline.commands.repair import repair_autopilot


def test_task_queue_deduplicates_active_file_tasks(tmp_path):
    queue = TaskQueue(tmp_path / "60-Logs" / "autopilot.db")

    first_id = queue.add_task(Task(source="pinboard", file_path="note.md"))
    second_id = queue.add_task(Task(source="pinboard", file_path="note.md"))

    assert second_id == first_id
    assert len(queue.get_pending(limit=10)) == 1


def test_repair_autopilot_marks_stale_processing_tasks(tmp_path):
    vault = tmp_path / "vault"
    db_path = vault / "60-Logs" / "autopilot.db"
    queue = TaskQueue(db_path)
    task_id = queue.add_task(Task(source="inbox", file_path="raw.md"))
    assert queue.claim_task(task_id) is True

    stale_time = (datetime.now() - timedelta(days=2)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET started_at = ?, created_at = ? WHERE id = ?",
            (stale_time, stale_time, task_id),
        )
        conn.commit()

    result = repair_autopilot(vault, dry_run=False)

    with sqlite3.connect(db_path) as conn:
        status, error = conn.execute(
            "SELECT status, error FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

    assert result["fixed"] == 1
    assert status == "failed"
    assert error == "repaired_stale_processing_task"
