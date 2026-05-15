"""Tests for the M25.2 ``/ops/items`` route.

The view this PR builds is the drilldown the M25 hybrid card primary
CTAs target.  The hard contract is **card N === page N**: both the
card primary count and the items list read from the same
``ops_state`` table with the same pack filter, so the operator
clicking ``Open 47 items →`` lands on exactly 47 rows.

These tests lock the contract:

* Unknown state → explicit "unavailable" + readable reason.
* Missing ``ops_state`` table → explicit reason, no crash.
* State filter works.
* Pagination metadata (offset / limit / next / prev) is correct.
* Sort direction inverts for NeedsAction (oldest-first; M25 plan
  open issue locked in).
* No ``date=`` filter — primary cards count "current" items and a
  date filter would break the card-N === page-N contract.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ovp_pipeline.ops_lifecycle import (
    ALL_STATES,
    STATE_ACCEPTED,
    STATE_NEEDS_ACTION,
    STATE_RECEIVED,
)
from ovp_pipeline.ops_state import rebuild
from ovp_pipeline.ui.view_models import (
    ITEMS_LIST_DEFAULT_LIMIT,
    ITEMS_LIST_MAX_LIMIT,
    build_items_list_payload,
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


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "60-Logs" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_AUDIT_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def _seed(db_path: Path, *, received: int = 0, failures: int = 0) -> None:
    """Seed ``received`` Received sources + ``failures`` NeedsAction
    sources, then rebuild the projection."""
    base = datetime(2026, 5, 13, 8, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(db_path)
    for i in range(received):
        ts = (base.replace(minute=i % 60)).isoformat()
        conn.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
            ("pipeline.jsonl", "article_intake_only",
             f"src-r-{i:03d}", "sess", ts, "{}"),
        )
    for j in range(failures):
        ts = (base.replace(minute=(j + 30) % 60)).isoformat()
        conn.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
            ("pipeline.jsonl", "absorb_parse_error",
             f"src-f-{j:03d}", "sess", ts, "{}"),
        )
    conn.commit()
    rebuild(conn, pack=PACK)
    conn.close()


# ── Unavailable / error paths ─────────────────────────────────────


def test_unknown_state_returns_unavailable_reason(tmp_path):
    payload = build_items_list_payload(
        tmp_path, state="GarbageState", pack_name=PACK
    )
    assert payload["available"] is False
    assert "unknown state" in payload["reason"]
    assert payload["rows"] == []


def test_missing_db_returns_unavailable_reason(tmp_path):
    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK
    )
    assert payload["available"] is False
    assert "knowledge_index" in payload["reason"]


def test_missing_projection_table_returns_unavailable_reason(tmp_path):
    _make_db(tmp_path)  # audit_events only — no ops_state
    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK
    )
    assert payload["available"] is False
    assert "ops_state" in payload["reason"]


# ── Happy path + contract ─────────────────────────────────────────


def test_returns_rows_for_state(tmp_path):
    db_path = _make_db(tmp_path)
    _seed(db_path, received=3)
    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK
    )
    assert payload["available"] is True
    assert payload["state"] == "Received"
    assert payload["total"] == 3
    assert len(payload["rows"]) == 3
    for row in payload["rows"]:
        assert row["item_kind"] == "source"
        assert row["item_id"].startswith("src-r-")


def test_card_count_equals_page_count_contract(tmp_path):
    """The M25 hybrid card promises ``Open N items →`` lands on
    exactly N rows.  The audit projection must agree with what the
    cards report — same table, same pack filter."""
    from ovp_pipeline.ui.view_models import _read_lifecycle_summary

    db_path = _make_db(tmp_path)
    _seed(db_path, received=4, failures=2)

    summary = _read_lifecycle_summary(tmp_path, pack=PACK)
    received_card_count = summary["counts"]["Received"]

    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK,
        limit=ITEMS_LIST_MAX_LIMIT,
    )
    assert payload["total"] == received_card_count


def test_state_filter_isolates_rows(tmp_path):
    db_path = _make_db(tmp_path)
    _seed(db_path, received=2, failures=3)
    received = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK
    )
    failures = build_items_list_payload(
        tmp_path, state="NeedsAction", pack_name=PACK
    )
    received_ids = {r["item_id"] for r in received["rows"]}
    failure_ids = {r["item_id"] for r in failures["rows"]}
    assert not received_ids & failure_ids
    assert received["total"] == 2
    assert failures["total"] == 3


def test_unfamiliar_pack_returns_empty_but_available(tmp_path):
    db_path = _make_db(tmp_path)
    _seed(db_path, received=2)
    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name="some-other-pack"
    )
    assert payload["available"] is True
    assert payload["total"] == 0
    assert payload["rows"] == []


# ── Pagination ────────────────────────────────────────────────────


def test_pagination_offset_limit(tmp_path):
    db_path = _make_db(tmp_path)
    _seed(db_path, received=10)
    page1 = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK,
        offset=0, limit=4,
    )
    page2 = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK,
        offset=4, limit=4,
    )
    page3 = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK,
        offset=8, limit=4,
    )
    assert page1["total"] == 10
    assert len(page1["rows"]) == 4
    assert len(page2["rows"]) == 4
    assert len(page3["rows"]) == 2  # only 2 left
    assert page1["next_offset"] == 4
    assert page2["next_offset"] == 8
    assert page3["next_offset"] is None  # final page

    # All rows distinct across pages.
    all_ids = (
        [r["item_id"] for r in page1["rows"]]
        + [r["item_id"] for r in page2["rows"]]
        + [r["item_id"] for r in page3["rows"]]
    )
    assert len(all_ids) == len(set(all_ids))


def test_prev_offset_set_when_paginated(tmp_path):
    db_path = _make_db(tmp_path)
    _seed(db_path, received=8)
    middle = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK,
        offset=4, limit=2,
    )
    assert middle["prev_offset"] == 2
    assert middle["next_offset"] == 6


def test_limit_clamped_to_max(tmp_path):
    db_path = _make_db(tmp_path)
    _seed(db_path, received=1)
    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK,
        limit=ITEMS_LIST_MAX_LIMIT * 10,
    )
    assert payload["limit"] == ITEMS_LIST_MAX_LIMIT


def test_invalid_limit_falls_back_to_default(tmp_path):
    """``limit=0`` is treated as "not provided" — falls back to the
    default rather than rendering an empty page."""
    db_path = _make_db(tmp_path)
    _seed(db_path, received=1)
    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK,
        limit=0,
    )
    assert payload["limit"] == ITEMS_LIST_DEFAULT_LIMIT


# ── Sort direction ────────────────────────────────────────────────


def test_needs_action_sorts_oldest_first(tmp_path):
    """NeedsAction surfaces oldest-blocker-first per the M25 plan
    open issue — operator wants to attack stale blockers first."""
    db_path = _make_db(tmp_path)
    _seed(db_path, failures=4)
    payload = build_items_list_payload(
        tmp_path, state="NeedsAction", pack_name=PACK
    )
    timestamps = [r["last_evidence_at"] for r in payload["rows"]]
    assert timestamps == sorted(timestamps), (
        "NeedsAction rows must be ASC-sorted (oldest first)"
    )


def test_received_sorts_newest_first(tmp_path):
    db_path = _make_db(tmp_path)
    _seed(db_path, received=4)
    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK
    )
    timestamps = [r["last_evidence_at"] for r in payload["rows"]]
    assert timestamps == sorted(timestamps, reverse=True), (
        "Received rows must be DESC-sorted (newest first)"
    )


# ── Row shape ─────────────────────────────────────────────────────


def test_row_carries_primary_href_for_object(tmp_path):
    """The renderer needs a clickable target; ``primary_href`` is
    that.  Object-kind items must point at ``/object?id=…``."""
    db_path = _make_db(tmp_path)
    # Insert a fake object row + matching ops_state row directly.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE objects (pack TEXT, object_id TEXT, "
        "object_kind TEXT, title TEXT, canonical_path TEXT, "
        "source_slug TEXT, source_url TEXT DEFAULT '', "
        "PRIMARY KEY (pack, object_id))"
    )
    conn.execute(
        "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, "obj-x", "evergreen", "Foo",
         "10-Knowledge/Evergreen/Foo.md", "src-x", ""),
    )
    conn.commit()
    rebuild(conn, pack=PACK)
    conn.close()
    payload = build_items_list_payload(
        tmp_path, state="Accepted", pack_name=PACK
    )
    obj_row = next(
        (r for r in payload["rows"] if r["item_kind"] == "object"),
        None,
    )
    assert obj_row is not None, "expected an object row in Accepted"
    assert obj_row["primary_href"].startswith("/object?id=obj-x")


def test_row_carries_evidence_event_types(tmp_path):
    db_path = _make_db(tmp_path)
    _seed(db_path, received=1)
    payload = build_items_list_payload(
        tmp_path, state="Received", pack_name=PACK
    )
    row = payload["rows"][0]
    assert "article_intake_only" in row["evidence_types"]
