"""M26 BL-104 — Workflow Progress projection.

Counts distinct ITEMS that ENTERED a lifecycle state on a day
(earliest qualifying evidence == that day), the transition-time
axis.  Distinct from Activity (event rows) and Current backlog
(right-now).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline.commands._ui_renderers import _render_workflow_progress_zone
from ovp_pipeline.ui.view_models import (
    build_today_digest_payload,
    build_workflow_progress_payload,
)

PACK = "research-tech"

_AUDIT = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL, event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL
);
"""


def _vault(tmp_path: Path, rows) -> Path:
    db = tmp_path / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(_AUDIT)
    conn.executemany("INSERT INTO audit_events VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return tmp_path


def _ev(et, slug, ts, payload=None):
    return ("pipeline.jsonl", et, slug, "s", ts, json.dumps(payload or {}))


def test_entered_state_on_earliest_evidence_day(tmp_path):
    """src-a: intake 05-09, extraction 05-10 → Received entered
    05-09, Extracted entered 05-10.  src-b: intake 05-10 only."""
    v = _vault(
        tmp_path,
        [
            _ev("article_intake_only", "src-a", "2026-05-09T09:00:00"),
            _ev("candidates_upserted", "src-a", "2026-05-10T09:00:00"),
            _ev("article_intake_only", "src-b", "2026-05-10T09:00:00"),
        ],
    )
    d10 = build_workflow_progress_payload(v, date_key="2026-05-10", pack=PACK)
    assert d10["moved"]["Received"] == 1  # src-b (src-a's Received was 05-09)
    assert d10["moved"]["Extracted"] == 1  # src-a entered Extracted 05-10
    d09 = build_workflow_progress_payload(v, date_key="2026-05-09", pack=PACK)
    assert d09["moved"]["Received"] == 1  # src-a
    assert d09["moved"]["Extracted"] == 0


def test_distinct_items_not_event_rows(tmp_path):
    """Many extraction rows for one source on D → 1 entered, not N."""
    v = _vault(
        tmp_path,
        [
            _ev("candidates_upserted", "src-a", "2026-05-10T09:00:00"),
            _ev("candidates_upserted", "src-a", "2026-05-10T10:00:00"),
            _ev("absorb_route_decision", "src-a", "2026-05-10T11:00:00"),
        ],
    )
    d = build_workflow_progress_payload(v, date_key="2026-05-10", pack=PACK)
    assert d["moved"]["Extracted"] == 1


def test_accepted_uses_object_identity(tmp_path):
    v = _vault(
        tmp_path,
        [
            _ev("evergreen_auto_promoted", "", "2026-05-10T09:00:00", {"concept": "obj-1"}),
            _ev("promote_concept", "", "2026-05-10T10:00:00", {"concept": "obj-1"}),
            _ev("evergreen_auto_promoted", "", "2026-05-10T11:00:00", {"concept": "obj-2"}),
        ],
    )
    d = build_workflow_progress_payload(v, date_key="2026-05-10", pack=PACK)
    assert d["moved"]["Accepted"] == 2  # obj-1, obj-2 distinct


def test_pack_scoping(tmp_path):
    v = _vault(
        tmp_path,
        [
            _ev("article_intake_only", "src-rt", "2026-05-10T09:00:00", {"pack": "research-tech"}),
            _ev("article_intake_only", "src-leg", "2026-05-10T09:00:00"),
            _ev("article_intake_only", "src-oth", "2026-05-10T09:00:00", {"pack": "other"}),
        ],
    )
    d = build_workflow_progress_payload(v, date_key="2026-05-10", pack="research-tech")
    assert d["moved"]["Received"] == 2  # rt + legacy, not other


def test_unavailable_without_db(tmp_path):
    d = build_workflow_progress_payload(tmp_path, date_key="2026-05-10", pack=PACK)
    assert d["available"] is False
    assert "knowledge_index" in d["reason"]


def test_payload_carries_workflow_progress(tmp_path):
    v = _vault(
        tmp_path,
        [_ev("candidates_upserted", "src-a", "2026-05-10T09:00:00")],
    )
    payload = build_today_digest_payload(v, pack_name=PACK, target_date="2026-05-10")
    assert "workflow_progress" in payload
    assert payload["workflow_progress"]["moved"]["Extracted"] == 1


def test_zone_render_distinguishes_axes(tmp_path):
    v = _vault(
        tmp_path,
        [_ev("candidates_upserted", "src-a", "2026-05-10T09:00:00")],
    )
    wp = build_workflow_progress_payload(v, date_key="2026-05-10", pack=PACK)
    html = _render_workflow_progress_zone(wp, "2026-05-10")
    assert "Workflow progress" in html
    assert "<strong>1</strong> item" in html
    assert "Extracted" in html
    # BL-100: must say it is NOT event rows / right-now backlog
    assert "not event rows" in html


def test_zone_render_empty_and_unavailable():
    empty = _render_workflow_progress_zone(
        {"available": True, "total": 0, "moved": {}}, "2026-05-10"
    )
    assert "No items changed lifecycle state" in empty
    unavail = _render_workflow_progress_zone(
        {"available": False, "reason": "knowledge_index has not been built yet"},
        "2026-05-10",
    )
    assert "knowledge_index" in unavail
