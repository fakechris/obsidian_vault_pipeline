from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from openclaw_pipeline.autopilot.queue import Task, TaskQueue
from openclaw_pipeline.autopilot.daemon import AutoPilotDaemon
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


def test_autopilot_success_path_runs_absorb_and_knowledge_index_after_moc(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    daemon = AutoPilotDaemon(vault, watch_sources=["inbox"], auto_commit=False)

    order: list[str] = []

    monkeypatch.setattr(
        "openclaw_pipeline.autopilot.daemon.subprocess.run",
        lambda *args, **kwargs: type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})(),
    )
    monkeypatch.setattr(daemon, "_check_quality", lambda task: (4.0, {}))
    monkeypatch.setattr(daemon, "_run_absorb", lambda: order.append("absorb"))
    monkeypatch.setattr(daemon, "_run_moc_update", lambda: order.append("moc"))
    monkeypatch.setattr(daemon, "_run_knowledge_index_refresh", lambda: order.append("knowledge_index"))

    task = Task(id=1, source="inbox", file_path=str(vault / "50-Inbox" / "01-Raw" / "note.md"))
    result = daemon.process_task(task)

    assert result["success"] is True
    assert result["stages"] == ["interpretation", "absorb", "moc", "knowledge_index"]
    assert order == ["absorb", "moc", "knowledge_index"]


def test_autopilot_with_refine_runs_refine_before_knowledge_index(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    daemon = AutoPilotDaemon(vault, watch_sources=["inbox"], auto_commit=False, with_refine=True)

    order: list[str] = []

    monkeypatch.setattr(
        "openclaw_pipeline.autopilot.daemon.subprocess.run",
        lambda *args, **kwargs: type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})(),
    )
    monkeypatch.setattr(daemon, "_check_quality", lambda task: (4.0, {}))
    monkeypatch.setattr(daemon, "_run_absorb", lambda: order.append("absorb"))
    monkeypatch.setattr(daemon, "_run_moc_update", lambda: order.append("moc"))
    monkeypatch.setattr(daemon, "_run_refine", lambda: order.append("refine"))
    monkeypatch.setattr(daemon, "_run_knowledge_index_refresh", lambda: order.append("knowledge_index"))

    task = Task(id=1, source="inbox", file_path=str(vault / "50-Inbox" / "01-Raw" / "note.md"))
    result = daemon.process_task(task)

    assert result["success"] is True
    assert result["stages"] == ["interpretation", "absorb", "moc", "refine", "knowledge_index"]
    assert order == ["absorb", "moc", "refine", "knowledge_index"]
