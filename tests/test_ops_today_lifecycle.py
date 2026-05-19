"""Tests for M24.4 lifecycle_summary on ``/ops/today``.

The cards continue to count today's audit_events (intake / absorb /
synthesis / governance / failures — the M24.0 stop-gap vocabulary).
The new ``lifecycle_summary`` payload field exposes the orthogonal
"how many items are sitting in each lifecycle state right now"
question, sourced from the ``ops_state`` projection that M24.1
builds.

Tests cover three regimes:

* ``ops_state`` table doesn't exist → the payload surfaces an
  explicit "projection not built yet" reason rather than silently
  returning zero counts.
* ``ops_state`` table exists but the requested pack has no rows
  → ``counts`` has every state with value 0; ``available`` is True.
* ``ops_state`` table exists with rows for the pack → ``counts``
  matches what ``ops_state.rebuild`` wrote.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ovp_pipeline.ops_lifecycle import (
    ALL_STATES,
    STATE_ACCEPTED,
    STATE_NEEDS_ACTION,
    STATE_RECEIVED,
)
from ovp_pipeline.ops_state import rebuild
from ovp_pipeline.ui.view_models import build_today_digest_payload


PACK = "research-tech"


_AUDIT_EVENTS_SCHEMA = """
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
    conn.executescript(_AUDIT_EVENTS_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def _seed_two_items(db_path: Path) -> None:
    """One Received source + one NeedsAction source (different slugs).

    Wires through ``ops_state.rebuild`` so the projection is whatever
    the kernel would derive from the audit log — keeps the test
    honest about the kernel/projection contract.
    """
    today = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("pipeline.jsonl", "article_intake_only", "src-1",
             "sess", today, "{}"),
            ("pipeline.jsonl", "absorb_parse_error", "src-2",
             "sess", today, "{}"),
        ],
    )
    conn.commit()
    rebuild(conn, pack=PACK)
    conn.close()


# ── ops_state table absent ────────────────────────────────────────


def test_lifecycle_summary_unavailable_when_projection_missing(tmp_path):
    """``audit_events`` exists but ``ops_state`` table has not been
    built — payload must say so explicitly."""
    _make_db(tmp_path)  # audit_events only
    payload = build_today_digest_payload(tmp_path, pack_name=PACK)
    summary = payload.get("lifecycle_summary") or {}
    assert summary.get("available") is False
    assert "ops_state" in (summary.get("reason") or "")


def test_lifecycle_summary_unavailable_when_db_missing(tmp_path):
    """No ``knowledge.db`` at all — the cards already short-circuit,
    and the lifecycle summary must match."""
    payload = build_today_digest_payload(tmp_path, pack_name=PACK)
    assert payload["available"] is False
    # When the DB is missing, build_today_digest_payload returns
    # early before the lifecycle_summary helper runs.  The absence
    # of the field is the contract.
    assert "lifecycle_summary" not in payload


# ── projection present + populated ────────────────────────────────


def test_lifecycle_summary_returns_kernel_counts(tmp_path):
    db_path = _make_db(tmp_path)
    _seed_two_items(db_path)
    payload = build_today_digest_payload(tmp_path, pack_name=PACK)
    summary = payload["lifecycle_summary"]
    assert summary["available"] is True
    assert summary["pack"] == PACK
    counts = summary["counts"]
    assert set(counts.keys()) == set(ALL_STATES)
    assert counts[STATE_RECEIVED] == 1
    assert counts[STATE_NEEDS_ACTION] == 1
    assert summary["total"] == 2


def test_lifecycle_summary_empty_pack_returns_zero_counts(tmp_path):
    """``ops_state`` exists but no rows for the requested pack →
    every state is present with count 0, ``available`` is True."""
    db_path = _make_db(tmp_path)
    _seed_two_items(db_path)
    payload = build_today_digest_payload(
        tmp_path, pack_name="some-other-pack"
    )
    summary = payload["lifecycle_summary"]
    assert summary["available"] is True
    assert all(summary["counts"][s] == 0 for s in ALL_STATES)
    assert summary["total"] == 0


def test_lifecycle_summary_counts_match_projection_directly(tmp_path):
    """The summary count must equal what ``ops_state.rebuild``
    returned.  Regression guard against the view-model drifting from
    the projection's own count helper."""
    db_path = _make_db(tmp_path)
    _seed_two_items(db_path)
    # Re-rebuild and capture the counts.
    with sqlite3.connect(db_path) as conn:
        rebuild_counts = rebuild(conn, pack=PACK)
    payload = build_today_digest_payload(tmp_path, pack_name=PACK)
    assert payload["lifecycle_summary"]["counts"] == rebuild_counts


def test_cards_are_independent_of_lifecycle_summary(tmp_path):
    """Cards count today's events; lifecycle_summary counts items in
    each state.  An NeedsAction item (failure event) must still
    count on the failures card AND the NeedsAction lifecycle
    bucket — they're orthogonal, not duplicates."""
    db_path = _make_db(tmp_path)
    _seed_two_items(db_path)
    payload = build_today_digest_payload(tmp_path, pack_name=PACK)
    # M25.3: cards are now keyed on lifecycle states.  The
    # NeedsAction card carries BOTH numbers — secondary count
    # (today's failure events) and primary count (current items
    # in NeedsAction).  The plan explicitly forbids collapsing
    # them.
    na_card = next(c for c in payload["cards"] if c["id"] == "NeedsAction")
    assert na_card["event_count"] == 1  # 1 failure event today
    assert na_card["primary_count"] == 1  # 1 item in NeedsAction state
    assert payload["lifecycle_summary"]["counts"][STATE_NEEDS_ACTION] == 1


def test_today_payload_cached_by_db_mtime(tmp_path, monkeypatch):
    """Day-switch perf: a second call with the db unchanged is
    served from cache (the 6 heavy builders do NOT re-run); a db
    mtime change (rebuild / ops_state write) busts every cached
    date so the payload can never go stale."""
    import os

    from ovp_pipeline.ui.view_models import _layer3

    db_path = _make_db(tmp_path)
    _seed_two_items(db_path)

    calls = {"n": 0}
    real = _layer3._build_today_digest_payload_uncached

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(_layer3, "_build_today_digest_payload_uncached", counting)
    _layer3._TODAY_PAYLOAD_CACHE.clear()

    p1 = build_today_digest_payload(tmp_path, pack_name=PACK)
    assert calls["n"] == 1
    p2 = build_today_digest_payload(tmp_path, pack_name=PACK)
    assert calls["n"] == 1  # cache hit — builders NOT re-run
    assert p2 == p1  # identical payload

    # mutating the returned dict must not corrupt the cached copy
    p2["cards"] = "tampered"
    p3 = build_today_digest_payload(tmp_path, pack_name=PACK)
    assert calls["n"] == 1
    assert p3["cards"] != "tampered"

    # a db write (atomic rebuild / ops_state) changes st_mtime_ns →
    # cache busts → recompute, still correct.
    st = db_path.stat()
    os.utime(db_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    p4 = build_today_digest_payload(tmp_path, pack_name=PACK)
    assert calls["n"] == 2  # cache busted by mtime change
    na = next(c for c in p4["cards"] if c["id"] == "NeedsAction")
    assert na["primary_count"] == 1  # data still correct post-bust
