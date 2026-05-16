"""M26 BL-105 — "Flow by intake day" cohort view.

Answers "what happened to the articles I saved that day?" — the
operator's actual question, which event-time Activity cannot. A
source counts in cohort D iff its EARLIEST intake (operator-local
day) is D; the view then shows where those sources are now.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline.commands._ui_renderers import _render_intake_cohort_zone
from ovp_pipeline.ui.view_models import (
    build_intake_cohort_payload,
    build_today_digest_payload,
)

PACK = "research-tech"

_AUDIT = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL, event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL
);
"""
_OPS = """
CREATE TABLE ops_state (
    pack TEXT NOT NULL, item_kind TEXT NOT NULL, item_id TEXT NOT NULL,
    state TEXT NOT NULL, sub_state TEXT, last_evidence_at TEXT,
    evidence_event_types_json TEXT NOT NULL DEFAULT '[]',
    needs_action_reason TEXT, refreshed_at TEXT NOT NULL,
    PRIMARY KEY (pack, item_kind, item_id)
);
"""


def _vault(tmp_path: Path, audit_rows, ops_rows=None) -> Path:
    db = tmp_path / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(_AUDIT)
    conn.executemany("INSERT INTO audit_events VALUES (?,?,?,?,?,?)", audit_rows)
    conn.executescript(_OPS)
    if ops_rows:
        conn.executemany("INSERT INTO ops_state VALUES (?,?,?,?,?,?,?,?,?)", ops_rows)
    conn.commit()
    conn.close()
    return tmp_path


def _ev(et, slug, ts, payload=None):
    return ("pipeline.jsonl", et, slug, "s", ts, json.dumps(payload or {}))


def _ops(slug, state, refreshed="2026-05-16T00:00:00"):
    return (PACK, "source", slug, state, None, "", "[]", None, refreshed)


def test_cohort_membership_by_earliest_intake_day(tmp_path):
    """B was first seen on 01-01 then again on 05-10 — its cohort is
    01-01, NOT 05-10.  A first seen 05-10 → cohort 05-10."""
    v = _vault(
        tmp_path,
        [
            _ev("article_intake_only", "src-a", "2026-05-10T09:00:00"),
            _ev("article_intake_only", "src-b", "2026-01-01T09:00:00"),
            _ev("source_staged_for_processing", "src-b", "2026-05-10T09:00:00"),
        ],
        [_ops("src-a", "Extracted"), _ops("src-b", "Accepted")],
    )
    c10 = build_intake_cohort_payload(v, date_key="2026-05-10", pack=PACK)
    assert c10["cohort_size"] == 1  # only src-a
    assert c10["distribution"]["Extracted"] == 1
    c01 = build_intake_cohort_payload(v, date_key="2026-01-01", pack=PACK)
    assert c01["cohort_size"] == 1  # only src-b
    assert c01["distribution"]["Accepted"] == 1


def test_untracked_when_no_ops_state_row(tmp_path):
    v = _vault(
        tmp_path,
        [_ev("article_intake_only", "src-x", "2026-05-10T09:00:00")],
        [],  # no ops_state rows
    )
    c = build_intake_cohort_payload(v, date_key="2026-05-10", pack=PACK)
    assert c["cohort_size"] == 1
    assert c["untracked"] == 1
    assert sum(c["distribution"].values()) == 0


def test_stalled_counts_old_received_extracted(tmp_path):
    """A source intaken months ago and still Received is stalled."""
    v = _vault(
        tmp_path,
        [_ev("article_intake_only", "src-old", "2026-01-01T09:00:00")],
        [_ops("src-old", "Received")],
    )
    c = build_intake_cohort_payload(v, date_key="2026-01-01", pack=PACK)
    assert c["cohort_size"] == 1
    assert c["stalled"] == 1
    assert c["oldest_age_days"] > c["stall_days"]


def test_pack_scoping(tmp_path):
    v = _vault(
        tmp_path,
        [
            _ev("article_intake_only", "src-rt", "2026-05-10T09:00:00", {"pack": "research-tech"}),
            _ev("article_intake_only", "src-leg", "2026-05-10T09:00:00"),
            _ev("article_intake_only", "src-oth", "2026-05-10T09:00:00", {"pack": "other"}),
        ],
        [_ops("src-rt", "Received"), _ops("src-leg", "Received")],
    )
    c = build_intake_cohort_payload(v, date_key="2026-05-10", pack="research-tech")
    assert c["cohort_size"] == 2  # rt + legacy, not other


def test_unavailable_without_db(tmp_path):
    c = build_intake_cohort_payload(tmp_path, date_key="2026-05-10", pack=PACK)
    assert c["available"] is False
    assert "knowledge_index" in c["reason"]


def test_payload_carries_intake_cohort(tmp_path):
    v = _vault(
        tmp_path,
        [_ev("article_intake_only", "src-a", "2026-05-10T09:00:00")],
        [_ops("src-a", "Extracted")],
    )
    payload = build_today_digest_payload(v, pack_name=PACK, target_date="2026-05-10")
    assert "intake_cohort" in payload
    assert payload["intake_cohort"]["cohort_size"] == 1


def test_zone_render_distinguishes_from_activity(tmp_path):
    v = _vault(
        tmp_path,
        [_ev("article_intake_only", "src-a", "2026-05-10T09:00:00")],
        [_ops("src-a", "Extracted")],
    )
    c = build_intake_cohort_payload(v, date_key="2026-05-10", pack=PACK)
    html = _render_intake_cohort_zone(c, "2026-05-10")
    assert "Flow by intake day" in html
    assert "<strong>1</strong> source" in html
    assert "first entered intake on 2026-05-10" in html
    assert "Extracted" in html
    # BL-100: copy must say this is NOT the event-time Activity
    assert "Activity" in html


def test_zone_render_empty_and_unavailable():
    empty = _render_intake_cohort_zone({"available": True, "cohort_size": 0}, "2026-05-10")
    assert "No sources first entered intake" in empty
    unavail = _render_intake_cohort_zone(
        {"available": False, "reason": "knowledge_index has not been built yet"},
        "2026-05-10",
    )
    assert "knowledge_index" in unavail
