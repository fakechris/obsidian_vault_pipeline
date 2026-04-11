from __future__ import annotations

import json
from pathlib import Path

from openclaw_pipeline.runtime import VaultLayout


def test_collect_progress_snapshot_reads_counts_transactions_and_latest_event(temp_vault):
    layout = VaultLayout.from_vault(temp_vault)
    layout.raw_dir.mkdir(parents=True, exist_ok=True)
    layout.processing_dir.mkdir(parents=True, exist_ok=True)
    layout.processed_month_dir(__import__("datetime").datetime(2026, 4, 8)).mkdir(parents=True, exist_ok=True)
    layout.transactions_dir.mkdir(parents=True, exist_ok=True)
    layout.pipeline_reports_dir.mkdir(parents=True, exist_ok=True)
    layout.pipeline_log.parent.mkdir(parents=True, exist_ok=True)

    (layout.raw_dir / "a.md").write_text("# raw", encoding="utf-8")
    (layout.processing_dir / "b.md").write_text("# processing", encoding="utf-8")
    (layout.processed_month_dir(__import__("datetime").datetime(2026, 4, 8)) / "c.md").write_text("# processed", encoding="utf-8")
    deep = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04" / "note_深度解读.md"
    deep.parent.mkdir(parents=True, exist_ok=True)
    deep.write_text("# deep", encoding="utf-8")
    (temp_vault / "10-Knowledge" / "Evergreen" / "concept.md").write_text("# evergreen", encoding="utf-8")
    (temp_vault / "10-Knowledge" / "Evergreen" / "_Candidates" / "candidate.md").write_text("# candidate", encoding="utf-8")
    (temp_vault / "10-Knowledge" / "Atlas" / "Atlas-Index.md").write_text("# atlas", encoding="utf-8")

    (layout.transactions_dir / "txn-1.json").write_text(
        json.dumps(
            {
                "id": "txn-1",
                "type": "pipeline",
                "status": "in_progress",
                "checkpoint": "articles",
                "last_updated": "2026-04-09T00:15:00Z",
            }
        ),
        encoding="utf-8",
    )
    (layout.pipeline_log).write_text(
        json.dumps(
            {
                "timestamp": "2026-04-09T00:14:57.623149",
                "session_id": "s1",
                "event_type": "article_processed",
                "file": "demo.md",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = layout.pipeline_reports_dir / "pipeline-report-20260409-001500.md"
    report.write_text("# report", encoding="utf-8")

    from openclaw_pipeline.commands.watch_progress import collect_progress_snapshot

    snapshot = collect_progress_snapshot(temp_vault, process_lines=["python -m openclaw_pipeline.unified_pipeline_enhanced --vault-dir /tmp/vault --full"])

    assert snapshot["counts"] == {
        "raw": 1,
        "processing": 1,
        "processed": 1,
        "deep_dives": 1,
        "evergreen": 1,
        "candidates": 1,
        "atlas": 1,
    }
    assert snapshot["active_transactions"][0]["id"] == "txn-1"
    assert snapshot["latest_event"]["event_type"] == "article_processed"
    assert snapshot["latest_report"] == str(report)
    assert snapshot["active_processes"] == 1


def test_collect_progress_snapshot_ignores_report_removed_during_sort(temp_vault, monkeypatch):
    layout = VaultLayout.from_vault(temp_vault)
    layout.pipeline_reports_dir.mkdir(parents=True, exist_ok=True)
    report = layout.pipeline_reports_dir / "pipeline-report-20260409-001500.md"
    report.write_text("# report", encoding="utf-8")

    from openclaw_pipeline.commands import watch_progress

    original_stat = Path.stat
    calls = {"count": 0}

    def flaky_stat(self: Path, *args, **kwargs):
        if self == report:
            calls["count"] += 1
            if calls["count"] >= 1:
                report.unlink(missing_ok=True)
                raise FileNotFoundError(self)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(watch_progress.Path, "stat", flaky_stat)

    snapshot = watch_progress.collect_progress_snapshot(temp_vault, process_lines=[])

    assert snapshot["latest_report"] is None


def test_format_progress_snapshot_shows_deltas(temp_vault):
    from openclaw_pipeline.commands.watch_progress import format_progress_snapshot

    previous = {
        "counts": {
            "raw": 10,
            "processing": 1,
            "processed": 2,
            "deep_dives": 3,
            "evergreen": 4,
            "candidates": 5,
            "atlas": 1,
        },
        "active_processes": 1,
        "active_transactions": [],
        "latest_event": None,
        "latest_report": None,
    }
    current = {
        "timestamp": "2026-04-09T00:20:00Z",
        "vault_dir": str(temp_vault),
        "counts": {
            "raw": 8,
            "processing": 1,
            "processed": 4,
            "deep_dives": 5,
            "evergreen": 4,
            "candidates": 5,
            "atlas": 1,
        },
        "active_processes": 2,
        "active_transactions": [
            {
                "id": "txn-1",
                "type": "article-processing",
                "checkpoint": "process",
                "last_updated": "2026-04-09T00:19:59Z",
            }
        ],
        "latest_event": {"event_type": "article_processed", "file": "demo.md", "timestamp": "2026-04-09T00:19:57Z"},
        "latest_report": None,
    }

    rendered = format_progress_snapshot(current, previous)

    assert "Raw: 8 (-2)" in rendered
    assert "Processed: 4 (+2)" in rendered
    assert "Running processes: 2" in rendered
    assert "checkpoint=process" in rendered
    assert "article_processed" in rendered


def test_watch_progress_main_once_outputs_snapshot(temp_vault, monkeypatch, capsys):
    layout = VaultLayout.from_vault(temp_vault)
    layout.raw_dir.mkdir(parents=True, exist_ok=True)
    (layout.raw_dir / "a.md").write_text("# raw", encoding="utf-8")

    from openclaw_pipeline.commands import watch_progress

    monkeypatch.setattr(
        watch_progress,
        "detect_openclaw_process_lines",
        lambda vault_dir: ["python -m openclaw_pipeline.unified_pipeline_enhanced --vault-dir /tmp/vault --full"],
    )

    exit_code = watch_progress.main(["--vault-dir", str(temp_vault), "--once"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Vault progress" in captured.out
    assert "Raw: 1" in captured.out
