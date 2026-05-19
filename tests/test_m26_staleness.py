"""M26 BL-103a — staleness zero reasons on /ops/today.

A number (especially a zero) on the daily page is meaningless if
the operator can't tell whether it is current.  These lock the two
telemetry-free signals:

* ``audit_sync_stale`` — pipeline.jsonl advanced past
  knowledge.db.audit_events.
* ``projection_stale`` — ops_state was refreshed before the newest
  synced audit row.

``unknown`` must never imply freshness.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline.commands._ui_renderers import _render_staleness_banner
from ovp_pipeline.ui.view_models import (
    build_today_digest_payload,
    compute_today_staleness,
)

PACK = "research-tech"

_AUDIT_SCHEMA = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL,
    event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
"""
_OPS_SCHEMA = """
CREATE TABLE ops_state (
    pack TEXT NOT NULL, item_kind TEXT NOT NULL, item_id TEXT NOT NULL,
    state TEXT NOT NULL, sub_state TEXT, last_evidence_at TEXT,
    evidence_event_types_json TEXT NOT NULL DEFAULT '[]',
    needs_action_reason TEXT, refreshed_at TEXT NOT NULL,
    PRIMARY KEY (pack, item_kind, item_id)
);
"""


def _db(
    tmp_path: Path,
    *,
    audit_max: str | None,
    refreshed_at: str | None,
    with_ops: bool = True,
) -> Path:
    db = tmp_path / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(_AUDIT_SCHEMA)
    if audit_max:
        conn.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?)",
            ("pipeline.jsonl", "article_intake_only", "s", "x", audit_max, "{}"),
        )
    if with_ops:
        conn.executescript(_OPS_SCHEMA)
        if refreshed_at:
            conn.execute(
                "INSERT INTO ops_state VALUES (?,?,?,?,?,?,?,?,?)",
                (PACK, "source", "s1", "Received", None, "", "[]", None, refreshed_at),
            )
    conn.commit()
    conn.close()
    return tmp_path


def _jsonl(tmp_path: Path, ts: str) -> None:
    p = tmp_path / "60-Logs" / "pipeline.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"event_type": "article_intake_only", "timestamp": ts}) + "\n")


def test_current_when_jsonl_and_projection_keep_up(tmp_path):
    v = _db(tmp_path, audit_max="2026-05-10T10:00:00", refreshed_at="2026-05-10T10:01:00")
    _jsonl(tmp_path, "2026-05-10T10:00:30")
    s = compute_today_staleness(v, pack=PACK)
    assert s["summary"] == "current"
    assert s["audit_sync_stale"] is False
    assert s["projection_stale"] is False


def test_audit_sync_stale_when_jsonl_ahead(tmp_path):
    v = _db(tmp_path, audit_max="2026-05-10T10:00:00", refreshed_at="2026-05-10T10:00:10")
    _jsonl(tmp_path, "2026-05-10T15:00:00")  # 5h newer than synced DB
    s = compute_today_staleness(v, pack=PACK)
    assert s["audit_sync_stale"] is True
    assert s["summary"] == "audit_sync_stale"
    assert "ovp-refresh-ops" in s["detail"]


def test_current_with_mixed_timestamp_formats(tmp_path):
    v = _db(
        tmp_path,
        audit_max="2026-05-17T10:59:45Z",
        refreshed_at="2026-05-17T11:28:01Z",
    )
    db = tmp_path / "60-Logs" / "knowledge.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO audit_events VALUES (?,?,?,?,?,?)",
            (
                "pipeline.jsonl",
                "command_error",
                "",
                "x",
                "2026-05-17T04:26:21.639658-07:00",
                "{}",
            ),
        )
        conn.commit()
    _jsonl(tmp_path, "2026-05-17T04:26:21.639658-07:00")

    s = compute_today_staleness(v, pack=PACK)

    assert s["summary"] == "current"
    assert s["audit_sync_stale"] is False
    assert s["db_latest"] == "2026-05-17T11:26:21.639658+00:00"


def test_projection_stale_when_ops_state_behind_audit(tmp_path):
    v = _db(tmp_path, audit_max="2026-05-10T12:00:00", refreshed_at="2026-05-09T08:00:00")
    _jsonl(tmp_path, "2026-05-10T12:00:00")  # in sync with DB
    s = compute_today_staleness(v, pack=PACK)
    assert s["audit_sync_stale"] is False
    assert s["projection_stale"] is True
    assert s["summary"] == "projection_stale"


def test_unknown_when_no_db(tmp_path):
    # no knowledge.db, no pipeline.jsonl
    s = compute_today_staleness(tmp_path, pack=PACK)
    assert s["summary"] == "unknown"
    assert s["audit_sync_stale"] is None
    assert s["projection_stale"] is None


def test_unsynced_when_db_empty_but_jsonl_has_events(tmp_path):
    v = _db(tmp_path, audit_max=None, refreshed_at=None)
    _jsonl(tmp_path, "2026-05-10T10:00:00")
    s = compute_today_staleness(v, pack=PACK)
    assert s["audit_sync_stale"] is True


def test_projection_stale_when_audit_synced_no_ops_state(tmp_path):
    v = _db(tmp_path, audit_max="2026-05-10T10:00:00", refreshed_at=None, with_ops=False)
    _jsonl(tmp_path, "2026-05-10T10:00:00")
    s = compute_today_staleness(v, pack=PACK)
    assert s["audit_sync_stale"] is False
    assert s["projection_stale"] is True


def test_payload_carries_staleness(tmp_path):
    v = _db(tmp_path, audit_max="2026-05-10T10:00:00", refreshed_at="2026-05-10T10:00:30")
    _jsonl(tmp_path, "2026-05-10T10:00:10")
    payload = build_today_digest_payload(v, pack_name=PACK, target_date="2026-05-10")
    assert "staleness" in payload
    assert payload["staleness"]["summary"] == "current"


def test_banner_render_states():
    cur = _render_staleness_banner({"summary": "current", "detail": "ok"})
    assert "Projections current" in cur
    stale = _render_staleness_banner(
        {"summary": "audit_sync_stale", "detail": "run ovp-refresh-ops"}
    )
    assert "Stale" in stale and "ovp-refresh-ops" in stale
    unk = _render_staleness_banner({"summary": "unknown", "detail": "?"})
    assert "Run status unknown" in unk
    assert _render_staleness_banner({}) == ""
