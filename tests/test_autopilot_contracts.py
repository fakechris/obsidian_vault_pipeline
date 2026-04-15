from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from openclaw_pipeline.autopilot.queue import Task, TaskQueue
from openclaw_pipeline.autopilot.daemon import AutoPilotDaemon
from openclaw_pipeline.commands.repair import repair_autopilot
from openclaw_pipeline.autopilot.watcher import MultiSourceWatcher, WATCHDOG_AVAILABLE


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


def test_autopilot_accepts_explicit_default_pack_profile(tmp_path):
    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)

    daemon = AutoPilotDaemon(
        vault,
        watch_sources=["inbox"],
        auto_commit=False,
        pack="default-knowledge",
        profile="autopilot",
    )

    assert daemon.pack.name == "default-knowledge"
    assert daemon.workflow_profile.name == "autopilot"
    assert daemon.workflow_profile.stages == ["interpretation", "quality", "absorb", "moc", "knowledge_index"]


def test_autopilot_accepts_explicit_research_tech_pack_profile(tmp_path):
    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)

    daemon = AutoPilotDaemon(
        vault,
        watch_sources=["inbox"],
        auto_commit=False,
        pack="research-tech",
        profile="autopilot",
    )

    assert daemon.pack.name == "research-tech"
    assert daemon.workflow_profile.name == "autopilot"
    assert daemon.workflow_profile.stages == ["interpretation", "quality", "absorb", "moc", "knowledge_index"]


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


def test_autopilot_uses_handler_registry_for_follow_up_stages(tmp_path, monkeypatch):
    import openclaw_pipeline.autopilot.daemon as daemon_source

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    daemon = AutoPilotDaemon(vault, watch_sources=["inbox"], auto_commit=False)

    monkeypatch.setattr(
        daemon_source.subprocess,
        "run",
        lambda *args, **kwargs: type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})(),
    )
    monkeypatch.setattr(daemon, "_check_quality", lambda task: (4.0, {}))
    monkeypatch.setattr(daemon, "_run_absorb", lambda: (_ for _ in ()).throw(AssertionError("direct absorb dispatch")))
    monkeypatch.setattr(daemon, "_run_moc_update", lambda: (_ for _ in ()).throw(AssertionError("direct moc dispatch")))
    monkeypatch.setattr(
        daemon,
        "_run_knowledge_index_refresh",
        lambda: (_ for _ in ()).throw(AssertionError("direct knowledge_index dispatch")),
    )

    calls: list[str] = []

    def fake_execute_autopilot_stage_handler(daemon_runtime, stage, **kwargs):
        calls.append(stage)
        if stage == "interpretation":
            return {"skipped": False}
        if stage == "quality":
            return {"quality": 4.0, "quality_dimensions": {}}
        return {"stage": stage}

    monkeypatch.setattr(
        daemon_source,
        "execute_autopilot_stage_handler",
        fake_execute_autopilot_stage_handler,
        raising=False,
    )

    task = Task(id=1, source="inbox", file_path=str(vault / "50-Inbox" / "01-Raw" / "note.md"))
    result = daemon.process_task(task)

    assert result["success"] is True
    assert calls == ["interpretation", "quality", "absorb", "moc", "knowledge_index"]


def test_autopilot_knowledge_index_refresh_passes_pack(tmp_path, monkeypatch):
    import openclaw_pipeline.autopilot.daemon as daemon_source

    vault = tmp_path / "vault"
    (vault / "60-Logs").mkdir(parents=True)
    daemon = AutoPilotDaemon(
        vault,
        watch_sources=["inbox"],
        auto_commit=False,
        pack="default-knowledge",
    )

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(daemon_source.subprocess, "run", fake_run)

    daemon._run_knowledge_index_refresh()

    assert "--pack" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--pack") + 1] == "default-knowledge"


def test_autopilot_watcher_import_does_not_require_watchdog(tmp_path):
    watcher = MultiSourceWatcher({"inbox": tmp_path}, lambda source, path: None)

    assert watcher.source_map["inbox"] == tmp_path
    assert watcher.observer is None


def test_autopilot_realtime_watcher_behaves_cleanly_without_watchdog(tmp_path):
    watcher = MultiSourceWatcher({"inbox": tmp_path}, lambda source, path: None)

    if WATCHDOG_AVAILABLE:
        watcher.start_realtime()
        assert watcher.running is True
        watcher.stop_realtime()
        assert watcher.observer is None
        return

    try:
        watcher.start_realtime()
    except RuntimeError as exc:
        assert "watchdog" in str(exc)
    else:
        raise AssertionError("expected realtime watcher to require watchdog")
