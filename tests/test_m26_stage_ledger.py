"""M26 BL-103b — DAG-boundary stage telemetry + full zero reasons.

Locks: the stage_* wrap actually fires around a handler; the
read-time stage-run rollup; and the zero-reason taxonomy
(not_run / ran_no_input / ran_no_output / failed / telemetry_missing
/ healthy / staleness) so a 0 on /ops/today is always diagnosable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline import handler_registry as HR
from ovp_pipeline.ui import view_models as VM

PACK = "research-tech"
NO_STALE = {"audit_sync_stale": False, "projection_stale": False}

_AUDIT = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL, event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL
);
"""


# ── pure helpers ───────────────────────────────────────────────────


def test_stage_io_counts_prefers_known_keys_and_summary():
    assert HR._stage_io_counts({"input_count": 5, "output_count": 2}) == (5, 2)
    assert HR._stage_io_counts({"summary": {"files_processed": 9, "candidates_added": 3}}) == (9, 3)
    assert HR._stage_io_counts({}) == (None, None)
    # booleans are not counts
    assert HR._stage_io_counts({"input_count": True}) == (None, None)


def test_result_is_skip():
    assert HR._result_is_skip({"skipped": True})
    assert HR._result_is_skip({"status": "no_qualified_files"})
    assert HR._result_is_skip({"skip_reason": "nothing eligible"})
    assert not HR._result_is_skip({"status": "ok", "output_count": 3})


def test_coerce_result_dict_handles_to_dict_objects():
    class R:
        def to_dict(self):
            return {"input_count": 1}

    assert HR._coerce_result_dict(R()) == {"input_count": 1}
    assert HR._coerce_result_dict(None) == {}


# ── emit wrap actually fires ───────────────────────────────────────


def test_profile_stage_wrap_emits_started_and_completed(monkeypatch):
    emitted: list[tuple] = []

    def fake_emit(vault_dir, log, et, payload, *, session_id=None, pack=None):
        emitted.append((et, payload.get("stage"), payload))
        return {}

    monkeypatch.setattr("ovp_pipeline.handler_registry._emit_event", fake_emit)

    class _Spec:
        entrypoint = "x:y"

    class _Contract:
        handler_spec = _Spec()

    monkeypatch.setattr(HR, "resolve_stage_execution_contract", lambda **k: _Contract())
    monkeypatch.setattr(
        HR,
        "load_entrypoint",
        lambda _e: (lambda **kw: {"input_count": 4, "output_count": 1}),
    )

    class _Pipe:
        vault_dir = "/tmp/v"
        session_id = "sess-1"
        workflow_pack_name = PACK

    out = HR.execute_profile_stage_handler(_Pipe(), "absorb", pack_name=PACK)
    assert out == {"input_count": 4, "output_count": 1}
    kinds = [e[0] for e in emitted]
    assert kinds == ["stage_started", "stage_completed"]
    assert emitted[1][1] == "absorb"
    assert emitted[1][2]["input_count"] == 4
    assert emitted[1][2]["output_count"] == 1


def test_profile_stage_wrap_emits_failed_and_reraises(monkeypatch):
    emitted: list[str] = []
    monkeypatch.setattr(
        "ovp_pipeline.handler_registry._emit_event",
        lambda *a, **k: emitted.append(a[2]) or {},
    )

    class _Spec:
        entrypoint = "x:y"

    class _Contract:
        handler_spec = _Spec()

    def _boom(**kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(HR, "resolve_stage_execution_contract", lambda **k: _Contract())
    monkeypatch.setattr(HR, "load_entrypoint", lambda _e: _boom)

    class _Pipe:
        vault_dir = "/tmp/v"
        session_id = "s"
        workflow_pack_name = PACK

    import pytest

    with pytest.raises(RuntimeError, match="kaboom"):
        HR.execute_profile_stage_handler(_Pipe(), "moc", pack_name=PACK)
    assert emitted == ["stage_started", "stage_failed"]


def test_telemetry_failure_never_breaks_dag(monkeypatch):
    def _raise(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("ovp_pipeline.handler_registry._emit_event", _raise)

    class _Spec:
        entrypoint = "x:y"

    class _Contract:
        handler_spec = _Spec()

    monkeypatch.setattr(HR, "resolve_stage_execution_contract", lambda **k: _Contract())
    monkeypatch.setattr(HR, "load_entrypoint", lambda _e: (lambda **kw: {"ok": True}))

    class _Pipe:
        vault_dir = "/tmp/v"
        session_id = "s"
        workflow_pack_name = PACK

    # emit raising must not propagate.
    assert HR.execute_profile_stage_handler(_Pipe(), "absorb")["ok"] is True


# ── rollup + zero reasons ──────────────────────────────────────────


def _vault(tmp_path: Path, rows) -> Path:
    db = tmp_path / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(_AUDIT)
    conn.executemany("INSERT INTO audit_events VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def _sev(et, stage, ts, run="r1", pack=PACK, **extra):
    p = {"stage": stage, "run_id": run, "pack": pack, **extra}
    return ("pipeline.jsonl", et, "", "s", ts, json.dumps(p))


def test_stage_runs_for_day_latest_run_and_status(tmp_path):
    db = _vault(
        tmp_path,
        [
            _sev("stage_started", "absorb", "2026-05-10T08:00:00", run="r1"),
            _sev(
                "stage_completed",
                "absorb",
                "2026-05-10T08:05:00",
                run="r1",
                input_count=7,
                output_count=3,
            ),
            # later run same day failed → latest wins
            _sev("stage_started", "absorb", "2026-05-10T20:00:00", run="r2"),
            _sev("stage_failed", "absorb", "2026-05-10T20:01:00", run="r2"),
        ],
    )
    conn = sqlite3.connect(db)
    runs = VM._stage_runs_for_day(conn, "2026-05-10", PACK)
    conn.close()
    assert runs["absorb"]["status"] == "failed"


def test_zero_reason_taxonomy():
    zr = VM._zero_reason_for_card
    # NeedsAction zero is the healthy case
    assert zr("NeedsAction", {}, NO_STALE)[0] == "healthy"
    # staleness wins over stage analysis
    assert zr("Extracted", {}, {"audit_sync_stale": True})[0] == "audit_sync_stale"
    assert zr("Extracted", {}, {"projection_stale": True})[0] == "projection_stale"
    # no feeding-stage run
    assert zr("Extracted", {}, NO_STALE)[0] == "not_run"
    # failed
    assert zr("Extracted", {"absorb": {"status": "failed"}}, NO_STALE)[0] == "failed"
    # ran, zero inputs
    assert (
        zr(
            "Extracted",
            {"absorb": {"status": "completed", "input": 0, "output": 0}},
            NO_STALE,
        )[0]
        == "ran_no_input"
    )
    # ran inputs, zero outputs
    assert (
        zr(
            "Extracted",
            {"absorb": {"status": "completed", "input": 9, "output": 0}},
            NO_STALE,
        )[0]
        == "ran_no_output"
    )
    # produced output but nothing projected here
    assert (
        zr(
            "Extracted",
            {"absorb": {"status": "completed", "input": 9, "output": 4}},
            NO_STALE,
        )[0]
        == "telemetry_missing"
    )


def test_payload_cards_carry_zero_reason(tmp_path):
    # only stage rows, no lifecycle evidence → every card is 0 and
    # must carry a zero_reason.
    db = _vault(
        tmp_path,
        [_sev("stage_completed", "absorb", "2026-05-10T08:00:00", input_count=0, output_count=0)],
    )
    payload = VM.build_today_digest_payload(
        db.parent.parent, pack_name=PACK, target_date="2026-05-10"
    )
    for c in payload["cards"]:
        assert int(c["event_count"]) == 0
        assert c.get("zero_reason"), c["id"]
        assert c.get("zero_detail")
    na = next(c for c in payload["cards"] if c["id"] == "NeedsAction")
    assert na["zero_reason"] == "healthy"
