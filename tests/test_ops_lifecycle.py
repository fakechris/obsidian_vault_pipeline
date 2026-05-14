"""Tests for the lifecycle kernel (M24.1).

The kernel's only inputs are ``audit_events`` and a handful of
truth-projection tables.  Tests build an in-memory ``knowledge.db``
fixture per scenario so the kernel never reads from the real vault —
matches the kernel's own purity contract.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ovp_pipeline.ops_lifecycle import (
    ALL_STATES,
    STATE_ACCEPTED,
    STATE_EXTRACTED,
    STATE_NEEDS_ACTION,
    STATE_RECEIVED,
    STATE_SYNTHESIZED,
    SUBSTATE_PREPARED,
    SUBSTATE_PROJECTED,
    lifecycle_counts,
    lifecycle_state_of,
    lifecycle_states_for_kind,
)


PACK = "research-tech"


# ── Fixture builders ──────────────────────────────────────────────


def _make_db() -> sqlite3.Connection:
    """Build the minimum schema the kernel reads from."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE audit_events (
            source_log TEXT NOT NULL,
            event_type TEXT NOT NULL,
            slug TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL
        );
        CREATE TABLE objects (
            pack TEXT NOT NULL,
            object_id TEXT NOT NULL,
            object_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            canonical_path TEXT NOT NULL,
            source_slug TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (pack, object_id)
        );
        CREATE TABLE graph_clusters (
            pack TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            cluster_kind TEXT NOT NULL,
            label TEXT NOT NULL,
            center_object_id TEXT NOT NULL,
            member_object_ids_json TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY (pack, cluster_id)
        );
        CREATE TABLE community_crystals (
            pack TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            body_md TEXT NOT NULL,
            source_evergreen_slugs_json TEXT NOT NULL,
            synthesized_at TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (pack, cluster_id, synthesized_at)
        );
        CREATE TABLE evergreen_revisions (
            pack TEXT NOT NULL,
            object_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content_md TEXT NOT NULL,
            change_type TEXT NOT NULL,
            changed_by TEXT NOT NULL DEFAULT '',
            derived_at TEXT NOT NULL,
            change_note TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (pack, object_id, version)
        );
        """
    )
    return conn


def _emit(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    slug: str = "",
    ts: str | None = None,
    payload: dict | None = None,
) -> None:
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO audit_events "
        "  (source_log, event_type, slug, session_id, timestamp, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "pipeline.jsonl",
            event_type,
            slug,
            "test-session",
            ts,
            json.dumps(payload or {}),
        ),
    )
    conn.commit()


# ── State derivation ──────────────────────────────────────────────


def test_received_only_source_classifies_as_received():
    conn = _make_db()
    _emit(conn, "article_intake_only", slug="src-1")
    state = lifecycle_state_of(conn, "source", "src-1", pack=PACK)
    assert state is not None
    assert state.state == STATE_RECEIVED
    assert state.sub_state is None
    assert state.evidence == ("article_intake_only",)


def test_absorb_route_decision_pushes_to_extracted():
    conn = _make_db()
    _emit(conn, "article_intake_only", slug="src-2",
          ts="2026-05-13T08:00:00+00:00")
    _emit(conn, "absorb_route_decision", slug="src-2",
          ts="2026-05-13T08:01:00+00:00")
    state = lifecycle_state_of(conn, "source", "src-2", pack=PACK)
    assert state is not None
    assert state.state == STATE_EXTRACTED
    # Evidence is newest-first.
    assert state.evidence[0] == "absorb_route_decision"


def test_promote_concept_pushes_to_accepted():
    conn = _make_db()
    _emit(conn, "article_intake_only", slug="src-3",
          ts="2026-05-13T08:00:00+00:00")
    _emit(conn, "absorb_route_decision", slug="src-3",
          ts="2026-05-13T08:01:00+00:00")
    _emit(conn, "promote_concept", slug="src-3",
          ts="2026-05-13T08:02:00+00:00")
    state = lifecycle_state_of(conn, "source", "src-3", pack=PACK)
    assert state is not None
    assert state.state == STATE_ACCEPTED


def test_evergreen_auto_promoted_pushes_to_accepted():
    conn = _make_db()
    _emit(conn, "evergreen_extraction_complete", slug="src-4",
          ts="2026-05-13T08:00:00+00:00")
    _emit(conn, "candidates_upserted", slug="src-4",
          ts="2026-05-13T08:01:00+00:00")
    _emit(conn, "evergreen_auto_promoted", slug="src-4",
          ts="2026-05-13T08:02:00+00:00")
    state = lifecycle_state_of(conn, "source", "src-4", pack=PACK)
    assert state is not None
    assert state.state == STATE_ACCEPTED


def test_failure_event_dominates_other_categories():
    """A failure row must win over any non-failure evidence —
    otherwise an item that "succeeded then failed" hides in Accepted."""
    conn = _make_db()
    _emit(conn, "article_intake_only", slug="src-5",
          ts="2026-05-13T08:00:00+00:00")
    _emit(conn, "promote_concept", slug="src-5",
          ts="2026-05-13T08:01:00+00:00")
    _emit(conn, "absorb_parse_error", slug="src-5",
          ts="2026-05-13T08:02:00+00:00")
    state = lifecycle_state_of(conn, "source", "src-5", pack=PACK)
    assert state is not None
    assert state.state == STATE_NEEDS_ACTION
    assert state.needs_action_reason == "absorb_parse_error"


def test_prepared_substate_when_extraction_without_upsert():
    """``evergreen_extraction_complete`` without a downstream
    ``candidates_upserted`` is the Prepared internal sub-state — the
    producer believes it finished, the absorb-writer hasn't run."""
    conn = _make_db()
    _emit(conn, "article_intake_only", slug="src-6",
          ts="2026-05-13T08:00:00+00:00")
    _emit(conn, "evergreen_extraction_complete", slug="src-6",
          ts="2026-05-13T08:01:00+00:00")
    state = lifecycle_state_of(conn, "source", "src-6", pack=PACK)
    assert state is not None
    # No promote / route_decision happened, so it's still Received
    # at the visible-state level — but the sub_state surfaces the
    # producer-without-consumer gap for debugging.
    assert state.sub_state == SUBSTATE_PREPARED


def test_projected_substate_when_object_row_without_promote_event():
    """An ``objects`` row exists but no ``evergreen_auto_promoted`` or
    ``promote_concept`` audit row references the same object_id — the
    Projected sub-state.  Surfaces the disagreement between the
    derived projection and the audit ledger."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, "obj-x", "evergreen", "Title X",
         "10-Knowledge/Evergreen/X.md", "src-x", ""),
    )
    conn.commit()
    state = lifecycle_state_of(conn, "object", "obj-x", pack=PACK)
    assert state is not None
    assert state.sub_state == SUBSTATE_PROJECTED
    # Projection-only classification → Accepted (the projection
    # asserts the artifact exists), and the sub_state flags the
    # missing audit row.
    assert state.state == STATE_ACCEPTED


def test_no_evidence_returns_none():
    conn = _make_db()
    assert lifecycle_state_of(conn, "source", "ghost", pack=PACK) is None


def test_unknown_item_kind_raises():
    conn = _make_db()
    with pytest.raises(ValueError):
        lifecycle_state_of(conn, "garbage", "x", pack=PACK)


# ── Synthesized + freshness ───────────────────────────────────────


def test_synthesized_fresh_crystal_classifies_as_synthesized():
    """An active community_crystal whose ``synthesized_at`` is newer
    than any member revision → Synthesized."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, "cluster-1", "community", "Memory",
         "obj-1", json.dumps(["obj-1", "obj-2"]), 0.5),
    )
    conn.execute(
        "INSERT INTO evergreen_revisions "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (PACK, "obj-1", 1, "## body", "created", "absorber",
         "2026-05-10T08:00:00+00:00", ""),
    )
    conn.execute(
        "INSERT INTO community_crystals "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (PACK, "cluster-1", "## crystal body",
         json.dumps(["obj-1"]),
         "2026-05-11T08:00:00+00:00",
         "fake-model", "v1", ""),
    )
    _emit(conn, "community_crystal_synthesized", slug="",
          ts="2026-05-11T08:00:00+00:00",
          payload={"cluster_id": "cluster-1"})
    state = lifecycle_state_of(
        conn, "cluster", "cluster-1", pack=PACK
    )
    assert state is not None
    assert state.state == STATE_SYNTHESIZED


def test_synthesized_stale_crystal_demotes_to_accepted():
    """If a cluster's newest revision is newer than its crystal, the
    crystal is stale and the cluster's state is Accepted, not
    Synthesized (the freshness rule from
    docs/operational-lifecycle.md §4)."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, "cluster-2", "community", "Memory",
         "obj-2", json.dumps(["obj-2"]), 0.5),
    )
    conn.execute(
        "INSERT INTO community_crystals "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (PACK, "cluster-2", "## old crystal",
         json.dumps(["obj-2"]),
         "2026-05-10T08:00:00+00:00",
         "fake-model", "v1", ""),
    )
    conn.execute(
        "INSERT INTO evergreen_revisions "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (PACK, "obj-2", 1, "## new body", "edited", "operator",
         "2026-05-12T08:00:00+00:00", ""),
    )
    _emit(conn, "community_crystal_synthesized", slug="",
          ts="2026-05-10T08:00:00+00:00",
          payload={"cluster_id": "cluster-2"})
    state = lifecycle_state_of(
        conn, "cluster", "cluster-2", pack=PACK
    )
    assert state is not None
    assert state.state == STATE_ACCEPTED


def test_synthesized_with_superseded_crystal_demotes_to_accepted():
    """A community_crystal row with non-empty
    ``superseded_by_synthesized_at`` is no longer active — the
    cluster falls back to Accepted."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, "cluster-3", "community", "Topic",
         "obj-3", json.dumps(["obj-3"]), 0.5),
    )
    conn.execute(
        "INSERT INTO community_crystals "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (PACK, "cluster-3", "## body",
         json.dumps(["obj-3"]),
         "2026-05-11T08:00:00+00:00",
         "fake-model", "v1",
         "2026-05-12T08:00:00+00:00"),  # superseded
    )
    _emit(conn, "community_crystal_synthesized", slug="",
          ts="2026-05-11T08:00:00+00:00",
          payload={"cluster_id": "cluster-3"})
    state = lifecycle_state_of(
        conn, "cluster", "cluster-3", pack=PACK
    )
    assert state is not None
    assert state.state == STATE_ACCEPTED


# ── Bulk + counts ─────────────────────────────────────────────────


def test_lifecycle_counts_has_all_five_buckets():
    """Even with zero items, ``lifecycle_counts`` returns a dict with
    all five states present (callers shouldn't have to pad zeros)."""
    conn = _make_db()
    counts = lifecycle_counts(conn, pack=PACK)
    assert set(counts.keys()) == set(ALL_STATES)
    assert all(v == 0 for v in counts.values())


def test_lifecycle_counts_aggregates_across_kinds():
    conn = _make_db()
    # One Received source.
    _emit(conn, "article_intake_only", slug="src-a")
    # One Accepted object via projection.
    conn.execute(
        "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, "obj-a", "evergreen", "T",
         "10-Knowledge/Evergreen/A.md", "src-a", ""),
    )
    conn.commit()
    counts = lifecycle_counts(conn, pack=PACK)
    assert counts[STATE_RECEIVED] == 1
    assert counts[STATE_ACCEPTED] == 1
    assert sum(counts.values()) == 2


def test_lifecycle_states_for_kind_yields_in_id_order():
    conn = _make_db()
    _emit(conn, "article_intake_only", slug="src-z")
    _emit(conn, "article_intake_only", slug="src-a")
    _emit(conn, "article_intake_only", slug="src-m")
    states = list(lifecycle_states_for_kind(conn, "source", pack=PACK))
    assert [s.item_id for s in states] == ["src-a", "src-m", "src-z"]


# ── Debug-only events don't move state ────────────────────────────


def test_debug_only_event_does_not_classify_alone():
    """A non-user-visible event like ``transaction_started`` must
    NOT push an item into a state on its own — those rows are
    forensic only."""
    conn = _make_db()
    _emit(conn, "transaction_started", slug="src-debug")
    state = lifecycle_state_of(conn, "source", "src-debug", pack=PACK)
    assert state is not None
    # Falls back to Received with Prepared sub-state because the
    # kernel saw evidence but none of it user_visible — flag the
    # gap rather than hide it.
    assert state.state == STATE_RECEIVED
    assert state.sub_state == SUBSTATE_PREPARED
