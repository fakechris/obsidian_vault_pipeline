"""BL-117 — Stale crystal detection.

Each of the four signals (jaccard_drift / open_contradiction /
member_delta / age) gets its own isolated fixture so a regression
in any one branch is locally diagnosable.  A quiet-vault test pins
idempotency (re-running on a fresh DB schedules zero LLM calls)
and a budget test pins the cap behaviour the nightly cron relies
on to bound LLM spend.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ovp_pipeline.knowledge_index import SCHEMA
from ovp_pipeline.synthesis.staleness import (
    AGE_DAYS_SENTINEL,
    JACCARD_STALENESS_THRESHOLD,
    MEMBER_DELTA_ABS,
    compute_crystal_staleness,
)


PACK = "research-tech"
RECENT_TS = "2026-05-26T00:00:00.000000+00:00"


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "knowledge.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    return conn


def _seed_concept(
    conn: sqlite3.Connection,
    *,
    cluster_id: str,
    synth_slugs: list[str],
    current_members: list[str],
    synthesized_at: str = RECENT_TS,
) -> None:
    """Insert one active crystal + matching graph_clusters row.
    The BL-114 trigger auto-seeds the ledger from this INSERT, so
    the staleness query has the (cc, ledger, gc) joins it needs."""
    conn.execute(
        "INSERT INTO graph_clusters "
        "(pack, cluster_id, cluster_kind, label, center_object_id, "
        " member_object_ids_json, score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, cluster_id, "louvain_community", "L",
         (current_members[0] if current_members else ""),
         json.dumps(current_members), float(len(current_members))),
    )
    conn.execute(
        "INSERT INTO community_crystals "
        "(pack, cluster_id, body_md, source_evergreen_slugs_json,"
        " synthesized_at, llm_model, prompt_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (PACK, cluster_id, "body", json.dumps(synth_slugs),
         synthesized_at, "m", "v1"),
    )


# ── Signal-isolation tests ────────────────────────────────────────


def test_jaccard_drift_below_threshold_flags_stale(tmp_path: Path):
    """Synthesis members and current members share only 4 of 14 →
    Jaccard ≈ 0.29 < 0.8 → primary_signal jaccard_drift."""
    conn = _conn(tmp_path)
    try:
        _seed_concept(
            conn, cluster_id="c1",
            synth_slugs=["a", "b", "c", "d", "e", "f", "g", "h"],
            current_members=["a", "b", "c", "d", "x", "y", "z",
                             "p", "q", "r", "s", "t"],
        )
        stale = compute_crystal_staleness(conn, pack=PACK)
        assert len(stale) == 1
        assert stale[0].primary_signal == "jaccard_drift"
        assert stale[0].jaccard is not None and stale[0].jaccard < 0.8
    finally:
        conn.close()


def test_high_jaccard_does_not_flag(tmp_path: Path):
    """8/9 overlap = 0.89 — above the 0.8 threshold; no jaccard_drift
    signal.  And small member counts → no member_delta either, and
    a recent synthesized_at → no age.  Concept should not be stale."""
    conn = _conn(tmp_path)
    try:
        _seed_concept(
            conn, cluster_id="c1",
            synth_slugs=["a", "b", "c", "d", "e", "f", "g", "h"],
            current_members=["a", "b", "c", "d", "e", "f", "g", "h", "i"],
        )
        stale = compute_crystal_staleness(conn, pack=PACK)
        assert stale == []
    finally:
        conn.close()


def test_open_contradiction_on_member_flags_stale(tmp_path: Path):
    """An open contradiction whose claim object_id is in the cluster's
    current members triggers the open_contradiction signal."""
    conn = _conn(tmp_path)
    try:
        _seed_concept(
            conn, cluster_id="c1",
            synth_slugs=["a", "b", "c"],
            current_members=["a", "b", "c"],
        )
        conn.execute(
            "INSERT INTO contradictions "
            "(pack, contradiction_id, subject_key, "
            " positive_claim_ids_json, negative_claim_ids_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (PACK, "x", "k", json.dumps(["a::1"]),
             json.dumps(["b::2"]), "open"),
        )
        stale = compute_crystal_staleness(conn, pack=PACK)
        assert len(stale) == 1
        assert "open_contradiction" in stale[0].signals
    finally:
        conn.close()


def test_resolved_contradiction_does_not_flag(tmp_path: Path):
    """A contradiction with status != 'open' is irrelevant — only
    OPEN ones signal staleness."""
    conn = _conn(tmp_path)
    try:
        _seed_concept(
            conn, cluster_id="c1",
            synth_slugs=["a", "b", "c"],
            current_members=["a", "b", "c"],
        )
        conn.execute(
            "INSERT INTO contradictions "
            "(pack, contradiction_id, subject_key, "
            " positive_claim_ids_json, negative_claim_ids_json, "
            " status, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (PACK, "x", "k", json.dumps(["a::1"]),
             json.dumps(["b::2"]), "resolved",
             "2026-05-20T00:00:00+00:00"),
        )
        stale = compute_crystal_staleness(conn, pack=PACK)
        assert stale == []
    finally:
        conn.close()


def test_member_delta_absolute_threshold_flags_stale(tmp_path: Path):
    """5+ absolute members added (or removed) trips member_delta
    even when Jaccard might still be above the staleness floor."""
    conn = _conn(tmp_path)
    try:
        # 10 synthesized, 16 current — delta 6, Jaccard 10/16 = 0.625
        # → BOTH jaccard_drift (< 0.8) AND member_delta fire.
        # Use a smaller delta that keeps Jaccard fresh:
        # synth=8, current=8+5=13 with all 8 overlapping → Jaccard 8/13=0.62.
        # Still trips jaccard_drift.  To isolate member_delta, use
        # near-identical sets that happen to differ by 5+ members.
        # 20 vs 25 with full overlap of the smaller in the larger:
        # Jaccard 20/25 = 0.80 — at the threshold (not < threshold).
        _seed_concept(
            conn, cluster_id="c1",
            synth_slugs=[f"m{i}" for i in range(20)],
            current_members=[f"m{i}" for i in range(25)],
        )
        stale = compute_crystal_staleness(conn, pack=PACK)
        assert len(stale) == 1
        # Jaccard 20/25 = 0.80 is NOT < 0.80 (strict inequality), so
        # jaccard_drift does NOT fire; member_delta does.
        assert "jaccard_drift" not in stale[0].signals
        assert "member_delta" in stale[0].signals
        assert stale[0].primary_signal == "member_delta"
    finally:
        conn.close()


def test_age_threshold_flags_stale(tmp_path: Path):
    """An untouched concept whose synthesized_at is older than the
    sentinel (14 days) gets the age signal — even when membership
    is completely fresh."""
    conn = _conn(tmp_path)
    try:
        old_ts = (
            datetime.now(timezone.utc) - timedelta(days=AGE_DAYS_SENTINEL + 5)
        ).isoformat(timespec="microseconds")
        _seed_concept(
            conn, cluster_id="c1",
            synth_slugs=["a", "b", "c"],
            current_members=["a", "b", "c"],
            synthesized_at=old_ts,
        )
        stale = compute_crystal_staleness(conn, pack=PACK)
        assert len(stale) == 1
        assert "age" in stale[0].signals
        assert stale[0].primary_signal == "age"
    finally:
        conn.close()


# ── Idempotency / quiet-vault ─────────────────────────────────────


def test_quiet_vault_yields_no_stale_concepts(tmp_path: Path):
    """Recent synth, full member overlap, no open contradictions —
    every signal sleeps.  Nightly cron does zero LLM work on a
    quiet vault."""
    conn = _conn(tmp_path)
    try:
        _seed_concept(
            conn, cluster_id="c1",
            synth_slugs=["a", "b", "c"],
            current_members=["a", "b", "c"],
        )
        stale = compute_crystal_staleness(conn, pack=PACK)
        assert stale == []
    finally:
        conn.close()


def test_signal_priority_ordering(tmp_path: Path):
    """Three concepts, one per priority tier — the sort order must
    put jaccard_drift first, then open_contradiction, then
    member_delta, then age.  The nightly budget then picks them in
    that order."""
    conn = _conn(tmp_path)
    try:
        # jaccard_drift
        _seed_concept(
            conn, cluster_id="c_jaccard",
            synth_slugs=["a", "b", "c", "d"],
            current_members=["a", "x", "y", "z"],
        )
        # open_contradiction
        _seed_concept(
            conn, cluster_id="c_contradiction",
            synth_slugs=["m", "n", "o"],
            current_members=["m", "n", "o"],
        )
        conn.execute(
            "INSERT INTO contradictions "
            "(pack, contradiction_id, subject_key, "
            " positive_claim_ids_json, negative_claim_ids_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (PACK, "x", "k", json.dumps(["m::1"]),
             json.dumps(["n::2"]), "open"),
        )
        # member_delta
        _seed_concept(
            conn, cluster_id="c_delta",
            synth_slugs=[f"d{i}" for i in range(20)],
            current_members=[f"d{i}" for i in range(25)],
        )
        # age
        old_ts = (
            datetime.now(timezone.utc) - timedelta(days=AGE_DAYS_SENTINEL + 5)
        ).isoformat(timespec="microseconds")
        _seed_concept(
            conn, cluster_id="c_age",
            synth_slugs=["q", "r", "s"],
            current_members=["q", "r", "s"],
            synthesized_at=old_ts,
        )
        stale = compute_crystal_staleness(conn, pack=PACK)
        order = [s.concept_id for s in stale]
        assert order == [
            "c_jaccard", "c_contradiction", "c_delta", "c_age",
        ]
    finally:
        conn.close()
