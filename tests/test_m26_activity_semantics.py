"""M26 BL-101/BL-102 — Activity cards count DISTINCT ITEMS, bucket
by operator-local day, and scope by pack; the card count equals the
drilldown's distinct-item count by construction.

These lock the product semantics M26 exists to fix: pre-M26 the
Activity cards counted raw ``audit_events`` rows (one source emits
several intake rows; one promote run emits one row per candidate),
so the headline overstated reality 2-5x and the operator could not
read item-level workflow movement off the page.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ovp_pipeline import audit_time
from ovp_pipeline.ui.view_models import (
    build_events_audit_payload,
    build_today_digest_payload,
)

_SCHEMA = """
CREATE TABLE audit_events (
    source_log TEXT NOT NULL,
    event_type TEXT NOT NULL,
    slug TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL
);
"""

DAY = "2026-05-10"
TS = f"{DAY}T10:00:00"  # naive → operator-local; stays on DAY


def _seed(tmp_path: Path, rows: list[tuple]) -> Path:
    db = tmp_path / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.executemany("INSERT INTO audit_events VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return tmp_path


def _row(et, slug="", ts=TS, payload=None):
    return ("pipeline.jsonl", et, slug, "s", ts, json.dumps(payload or {}))


# ── BL-101: distinct items, not raw event rows ─────────────────────


def test_received_counts_distinct_sources_not_event_rows(tmp_path):
    """One source emits several intake rows; the card must show 2
    sources, not 4 rows."""
    v = _seed(
        tmp_path,
        [
            _row("article_intake_only", "src-a"),
            _row("source_staged_for_processing", "src-a"),
            _row("source_archived_to_processed", "src-a"),
            _row("article_intake_only", "src-b"),
        ],
    )
    digest = build_today_digest_payload(v, target_date=DAY)
    recv = next(c for c in digest["cards"] if c["id"] == "Received")
    assert recv["event_count"] == 2  # distinct sources
    # raw-row breakdown is preserved for the drilldown reconciliation
    assert sum(recv["event_by_type"].values()) == 4


def test_accepted_counts_distinct_objects_not_promote_rows(tmp_path):
    """One absorb run emits one promote row per candidate; the
    Accepted card must count distinct objects."""
    v = _seed(
        tmp_path,
        [
            _row("evergreen_auto_promoted", "", payload={"concept": "obj-1"}),
            _row("evergreen_auto_promoted", "", payload={"concept": "obj-1"}),
            _row("evergreen_auto_promoted", "", payload={"concept": "obj-2"}),
        ],
    )
    digest = build_today_digest_payload(v, target_date=DAY)
    acc = next(c for c in digest["cards"] if c["id"] == "Accepted")
    assert acc["event_count"] == 2
    assert sum(acc["event_by_type"].values()) == 3


# ── Success criterion: card == drilldown distinct items ────────────


def test_card_count_equals_drilldown_distinct_items_all_states(tmp_path):
    """Independently computed: the card's distinct-item count must
    equal the drilldown's distinct_item_count, for every state."""
    v = _seed(
        tmp_path,
        [
            # Received: 2 sources, 3 rows
            _row("article_intake_only", "src-a"),
            _row("source_staged_for_processing", "src-a"),
            _row("article_intake_only", "src-b"),
            # Extracted: 2 sources, 3 rows
            _row("candidates_upserted", "src-a"),
            _row("candidates_upserted", "src-a"),
            _row("absorb_route_decision", "src-c"),
            # Accepted: 2 objects, 3 rows
            _row("evergreen_auto_promoted", "", payload={"concept": "obj-1"}),
            _row("evergreen_auto_promoted", "", payload={"concept": "obj-1"}),
            _row("promote_concept", "", payload={"concept": "obj-2"}),
            # Synthesized: 2 clusters, 3 rows
            _row("community_crystal_synthesized", "", payload={"cluster_id": "cl-1"}),
            _row("community_crystal_synthesized", "", payload={"cluster_id": "cl-1"}),
            _row("contradiction_crystal_synthesized", "", payload={"cluster_id": "cl-2"}),
            # NeedsAction: 2 sources, 3 rows
            _row("absorb_parse_error", "src-x"),
            _row("absorb_parse_error", "src-x"),
            _row("absorb_schema_drift", "src-y"),
        ],
    )
    digest = build_today_digest_payload(v, target_date=DAY)
    seen = set()
    for card in digest["cards"]:
        if not card["event_types"]:
            continue
        audit = build_events_audit_payload(
            v,
            event_types=tuple(card["event_types"]),
            date_key=DAY,
            pack_name="research-tech",
        )
        assert audit["distinct_item_count"] == card["event_count"], (
            f"{card['id']}: card={card['event_count']} "
            f"drilldown_distinct={audit['distinct_item_count']}"
        )
        # raw evidence rows are still all present and >= item count
        assert audit["total"] >= card["event_count"]
        seen.add(card["id"])
    assert {"Received", "Extracted", "Accepted", "Synthesized", "NeedsAction"} <= seen


# ── BL-102: operator-local day bucketing ───────────────────────────


def test_local_day_same_for_utc_z_and_naive_local_same_instant():
    """The core BL-102 guarantee: a UTC-Z row and the naive-local
    string for the SAME instant bucket to the same operator day.
    SQLite date() over mixed strings does NOT guarantee this."""
    utc_dt = datetime(2026, 5, 10, 23, 30, tzinfo=timezone.utc)
    expected = utc_dt.astimezone().date().isoformat()
    z_str = "2026-05-10T23:30:00Z"
    naive_local = utc_dt.astimezone().replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
    assert audit_time.local_day(z_str) == expected
    assert audit_time.local_day(naive_local) == expected
    assert audit_time.local_day("") is None
    assert audit_time.local_day("garbage") is None


def test_card_buckets_utc_z_row_by_local_day(tmp_path):
    """A UTC-Z intake row must land on its operator-local day, not
    its raw UTC date prefix."""
    utc_dt = datetime(2026, 5, 10, 23, 30, tzinfo=timezone.utc)
    local_day = utc_dt.astimezone().date().isoformat()
    z_ts = "2026-05-10T23:30:00Z"
    v = _seed(tmp_path, [_row("article_intake_only", "src-z", ts=z_ts)])
    digest = build_today_digest_payload(v, target_date=local_day)
    recv = next(c for c in digest["cards"] if c["id"] == "Received")
    assert recv["event_count"] == 1


# ── BL-102: pack scoping ───────────────────────────────────────────


def test_pack_scoping_default_pack_includes_legacy_pack_less(tmp_path):
    """Under the default pack: matching pack + legacy pack-less rows
    counted, a different pack excluded."""
    v = _seed(
        tmp_path,
        [
            _row("article_intake_only", "src-rt", payload={"pack": "research-tech"}),
            _row("article_intake_only", "src-legacy"),  # no pack
            _row("article_intake_only", "src-other", payload={"pack": "other-pack"}),
        ],
    )
    digest = build_today_digest_payload(v, pack_name="research-tech", target_date=DAY)
    recv = next(c for c in digest["cards"] if c["id"] == "Received")
    assert recv["event_count"] == 2  # research-tech + legacy, not other


def test_pack_scoping_non_default_pack_excludes_legacy(tmp_path):
    """Under a non-default pack: legacy pack-less rows are NOT
    counted (they belong to the default pack only)."""
    v = _seed(
        tmp_path,
        [
            _row("article_intake_only", "src-x", payload={"pack": "pack-x"}),
            _row("article_intake_only", "src-legacy"),  # no pack
        ],
    )
    digest = build_today_digest_payload(v, pack_name="pack-x", target_date=DAY)
    recv = next(c for c in digest["cards"] if c["id"] == "Received")
    assert recv["event_count"] == 1  # only pack-x, legacy excluded
