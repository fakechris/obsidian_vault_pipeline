from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_transaction_manager_start_creates_run_ledger(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import TransactionManager

    txn = TransactionManager(tmp_path)
    txn_id = txn.start(
        "enhanced-pipeline",
        "Incremental pipeline",
        pack_name="research-tech",
        workflow_profile="full",
        planned_steps=["pinboard", "clippings", "articles"],
    )

    payload = json.loads((tmp_path / f"{txn_id}.json").read_text(encoding="utf-8"))

    assert payload["run_ledger"]["run_id"] == txn_id
    assert payload["run_ledger"]["run_state"] == "running"
    assert payload["run_ledger"]["pack_name"] == "research-tech"
    assert payload["run_ledger"]["workflow_profile"] == "full"
    assert payload["run_ledger"]["planned_steps"] == ["pinboard", "clippings", "articles"]
    assert payload["run_ledger"]["current_step"]["step_name"] == "initialized"
    assert payload["run_ledger"]["current_step"]["step_state"] == "pending"


def test_transaction_manager_write_preserves_previous_ledger_on_dump_failure(tmp_path, monkeypatch):
    import ovp_pipeline.unified_pipeline_enhanced as pipeline_module
    from ovp_pipeline.unified_pipeline_enhanced import TransactionManager

    txn = TransactionManager(tmp_path)
    txn_file = tmp_path / "txn-1.json"
    txn_file.write_text(json.dumps({"id": "txn-1", "status": "in_progress"}), encoding="utf-8")

    def broken_dump(_payload, fp, **_kwargs):
        fp.write('{"partial":')
        raise RuntimeError("simulated dump failure")

    monkeypatch.setattr(pipeline_module.json, "dump", broken_dump)

    with pytest.raises(RuntimeError, match="simulated dump failure"):
        txn._write("txn-1", {"id": "txn-1", "status": "completed"})

    assert json.loads(txn_file.read_text(encoding="utf-8")) == {"id": "txn-1", "status": "in_progress"}
    assert not list(tmp_path.glob(".txn-1.json*.tmp"))


def test_transaction_manager_tracks_step_progress_and_percent(tmp_path):
    from ovp_pipeline.unified_pipeline_enhanced import TransactionManager

    txn = TransactionManager(tmp_path)
    txn_id = txn.start("enhanced-pipeline", "Incremental pipeline")

    txn.step(
        txn_id,
        "absorb",
        "in_progress",
        progress_mode="counted",
        work_units_total=10,
        work_units_done=3,
        work_units_failed=1,
        current_item="Alpha_深度解读.md",
        progress_summary="3/10 files processed",
        last_meaningful_event={
            "event_type": "absorb_file_processed",
            "file": "Alpha_深度解读.md",
        },
    )

    payload = json.loads((tmp_path / f"{txn_id}.json").read_text(encoding="utf-8"))
    current = payload["run_ledger"]["current_step"]

    assert payload["run_ledger"]["current_step_name"] == "absorb"
    assert current["step_name"] == "absorb"
    assert current["step_state"] == "running"
    assert current["progress_mode"] == "counted"
    assert current["work_units_total"] == 10
    assert current["work_units_done"] == 3
    assert current["work_units_failed"] == 1
    assert current["current_item"] == "Alpha_深度解读.md"
    assert current["progress_percent"] == 30.0
    assert current["progress_summary"] == "3/10 files processed"
    assert payload["run_ledger"]["last_meaningful_event"]["event_type"] == "absorb_file_processed"


def test_transaction_step_transition_resets_previous_progress_fields():
    from ovp_pipeline.txn import build_transaction_payload, heartbeat_transaction, update_transaction_step

    payload = build_transaction_payload("txn-1", "enhanced-pipeline", "demo")
    update_transaction_step(
        payload,
        "absorb",
        "in_progress",
        progress_mode="counted",
        work_units_total=10,
        work_units_done=4,
        work_units_failed=1,
        current_item="Alpha_深度解读.md",
        progress_summary="4/10 files processed",
    )

    update_transaction_step(payload, "moc", "in_progress")

    current = payload["run_ledger"]["current_step"]
    assert current["step_name"] == "moc"
    assert current["progress_mode"] == "indeterminate"
    assert current["work_units_total"] is None
    assert current["work_units_done"] == 0
    assert current["work_units_failed"] == 0
    assert current["current_item"] is None
    assert current["progress_percent"] is None
    assert current["progress_summary"] == "Progress is currently indeterminate."

    heartbeat_transaction(payload, step_name="knowledge_index")

    current = payload["run_ledger"]["current_step"]
    assert current["step_name"] == "knowledge_index"
    assert current["progress_mode"] == "indeterminate"
    assert current["work_units_total"] is None
    assert current["work_units_done"] == 0
    assert current["work_units_failed"] == 0
    assert current["current_item"] is None
    assert current["progress_percent"] is None


def test_classify_run_ledgers_separates_active_and_stale(tmp_path):
    from ovp_pipeline.txn import classify_run_ledgers

    active = tmp_path / "txn-active.json"
    active.write_text(
        json.dumps(
            {
                "id": "txn-active",
                "status": "in_progress",
                "checkpoint": "absorb",
                "last_updated": "2026-04-18T12:05:00Z",
                "run_ledger": {
                    "run_id": "txn-active",
                    "run_state": "running",
                    "heartbeat_at": "2026-04-18T12:05:00Z",
                    "current_step": {
                        "step_name": "absorb",
                        "step_state": "running",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stale = tmp_path / "txn-stale.json"
    stale.write_text(
        json.dumps(
            {
                "id": "txn-stale",
                "status": "in_progress",
                "checkpoint": "fix_links",
                "last_updated": "2026-04-18T10:00:00Z",
                "run_ledger": {
                    "run_id": "txn-stale",
                    "run_state": "running",
                    "heartbeat_at": "2026-04-18T10:00:00Z",
                    "current_step": {
                        "step_name": "fix_links",
                        "step_state": "running",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    classified = classify_run_ledgers(
        tmp_path,
        now_iso="2026-04-18T12:10:00Z",
        stale_after_seconds=1800,
    )

    assert [item["id"] for item in classified["active"]] == ["txn-active"]
    assert [item["id"] for item in classified["stale"]] == ["txn-stale"]


def test_classify_run_ledgers_falls_back_when_now_iso_is_malformed(tmp_path):
    from ovp_pipeline.txn import classify_run_ledgers

    txn_file = tmp_path / "txn-active.json"
    txn_file.write_text(
        json.dumps(
            {
                "id": "txn-active",
                "status": "in_progress",
                "checkpoint": "absorb",
                "last_updated": "2026-04-18T12:05:00Z",
                "run_ledger": {
                    "run_id": "txn-active",
                    "run_state": "running",
                    "heartbeat_at": "2026-04-18T12:05:00Z",
                    "current_step": {
                        "step_name": "absorb",
                        "step_state": "running",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    classified = classify_run_ledgers(tmp_path, now_iso="not-a-timestamp", stale_after_seconds=10**9)

    assert [item["id"] for item in classified["active"]] == ["txn-active"]
