"""Tests for synthesis/crystal_scoring.py — BL-045 crystal_scores
Projection.

Three layers:

1. **Pure scoring math** — each ``_compute_*_signal`` function and
   the weighted sum are unit-tested without any DB.

2. **DB rebuild** — the full ``rebuild_crystal_scores`` flow is
   tested against a synthetic seeded vault so the SELECTs +
   normalization + INSERT path are exercised end-to-end.

3. **Architecture invariants** — re-running on unchanged input
   produces identical scores; an empty vault produces zero rows;
   missing source_authority table degrades gracefully.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ovp_pipeline.synthesis.crystal_scoring import (
    DEFAULT_WEIGHTS,
    ScoreSignals,
    ScoreWeights,
    _credibility_signal,
    _contradiction_signal,
    _evergreen_recency_signal,
    _reuse_recency_signal,
    _size_signal,
    compute_score,
    rebuild_crystal_scores,
)


SCHEMA = """
CREATE TABLE objects (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  title TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  PRIMARY KEY (pack, object_id)
);
CREATE TABLE claims (
  pack TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  claim_kind TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (pack, claim_id)
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
CREATE TABLE contradictions (
  pack TEXT NOT NULL,
  contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution_note TEXT NOT NULL DEFAULT '',
  resolved_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id)
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
CREATE TABLE contradiction_crystals (
  pack TEXT NOT NULL,
  contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  body_md TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  source_object_ids_json TEXT NOT NULL,
  synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id, synthesized_at)
);
CREATE TABLE crystal_scores (
  pack TEXT NOT NULL,
  crystal_kind TEXT NOT NULL,
  crystal_id TEXT NOT NULL,
  score REAL NOT NULL,
  size_norm REAL NOT NULL DEFAULT 0,
  credibility_norm REAL NOT NULL DEFAULT 0,
  contradiction_norm REAL NOT NULL DEFAULT 0,
  reuse_recency_norm REAL NOT NULL DEFAULT 0,
  evergreen_recency_norm REAL NOT NULL DEFAULT 0,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (pack, crystal_kind, crystal_id)
);
CREATE TABLE reuse_events (
  event_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL DEFAULT '',
  object_kind TEXT NOT NULL DEFAULT '',
  surface TEXT NOT NULL,
  consumer_ref TEXT NOT NULL DEFAULT '',
  evidence_present INTEGER NOT NULL DEFAULT 0,
  provenance_clean INTEGER NOT NULL DEFAULT 0,
  trusted INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE source_authority (
  source_id TEXT PRIMARY KEY,
  authority REAL NOT NULL,
  signals_json TEXT NOT NULL DEFAULT '{}',
  scored_at TEXT NOT NULL DEFAULT '',
  scorer_version TEXT NOT NULL DEFAULT ''
);
"""


# ---------------------------------------------------------------------------
# Pure scoring math
# ---------------------------------------------------------------------------


class TestSizeSignal:
    def test_zero_size_is_zero(self):
        assert _size_signal(0) == 0.0

    def test_log_scaling(self):
        # Log-scaled, so doubling size doesn't double the signal.
        s_small = _size_signal(10)
        s_big = _size_signal(100)
        assert 0 < s_small < s_big < 1.0
        # 10x size doesn't give 10x score (that's the point).
        assert s_big < s_small * 5

    def test_saturation_above_cap(self):
        # 600 > cap=500; should saturate at 1.0.
        assert _size_signal(600) == 1.0
        assert _size_signal(10000) == 1.0


class TestCredibilitySignal:
    def test_proportional_normalization(self):
        # Crystal with sum=5 in a pack where max=10 → 0.5.
        assert _credibility_signal(5.0, 10.0) == 0.5

    def test_max_observed_yields_one(self):
        assert _credibility_signal(10.0, 10.0) == 1.0

    def test_zero_max_yields_zero(self):
        # Empty pack — no source_authority data — degrades to 0.
        assert _credibility_signal(0.0, 0.0) == 0.0

    def test_clamped_above_max(self):
        # Defensive: never exceed 1.0 even on stale data.
        assert _credibility_signal(15.0, 10.0) == 1.0


class TestContradictionSignal:
    def test_zero_count_is_zero(self):
        assert _contradiction_signal(0, 5) == 0.0

    def test_max_observed_yields_one(self):
        assert _contradiction_signal(5, 5) == 1.0

    def test_zero_max_yields_zero(self):
        # No open contradictions in pack → all crystals score 0.
        assert _contradiction_signal(0, 0) == 0.0


class TestReuseRecencySignal:
    """BL-049 wires this signal to the ``reuse_events`` table.  In
    M14 v0 the signal was fixed at zero — these tests pin both the
    cold-start (zero) and once-data-flows (non-zero) paths."""

    def test_zero_count_is_zero(self):
        assert _reuse_recency_signal(0, 5) == 0.0

    def test_max_observed_yields_one(self):
        assert _reuse_recency_signal(5, 5) == 1.0

    def test_zero_max_yields_zero(self):
        # Cold start: nothing was reused → all crystals score 0.
        assert _reuse_recency_signal(0, 0) == 0.0

    def test_clamped_above_max(self):
        assert _reuse_recency_signal(10, 5) == 1.0

    def test_proportional_normalization(self):
        # 2 reuses out of a per-pack max of 8 → 0.25.
        assert _reuse_recency_signal(2, 8) == 0.25


class TestEvergreenRecencySignal:
    def test_today_yields_one(self):
        # mtime = now → fresh = 1.0.
        now = datetime.now(timezone.utc).timestamp()
        assert _evergreen_recency_signal(now, now_utc=now) == 1.0

    def test_past_window_yields_zero(self):
        now = 1_000_000.0
        old = now - 366 * 86400  # 366 days ago
        assert _evergreen_recency_signal(old, now_utc=now) == 0.0

    def test_linear_decay(self):
        # 6 months ago = roughly 0.5.
        now = 1_000_000.0
        half = now - 182.5 * 86400
        result = _evergreen_recency_signal(half, now_utc=now)
        assert 0.4 < result < 0.6

    def test_none_yields_zero(self):
        assert _evergreen_recency_signal(None) == 0.0


class TestComputeScore:
    def test_default_weights_sum_to_one(self):
        assert abs(DEFAULT_WEIGHTS.total() - 1.0) < 1e-6

    def test_all_zeros_is_zero(self):
        assert compute_score(ScoreSignals()) == 0.0

    def test_all_ones_yields_weights_total(self):
        signals = ScoreSignals(
            size_norm=1.0, credibility_norm=1.0, contradiction_norm=1.0,
            reuse_recency_norm=1.0, evergreen_recency_norm=1.0,
        )
        # When all signals are 1, score equals sum of weights.
        assert abs(compute_score(signals) - DEFAULT_WEIGHTS.total()) < 1e-6

    def test_weighted_combination(self):
        # Hand-computed: 0.25 * 1.0 + 0.30 * 0.5 = 0.40
        signals = ScoreSignals(size_norm=1.0, credibility_norm=0.5)
        assert abs(compute_score(signals) - 0.40) < 1e-6

    def test_custom_weights(self):
        signals = ScoreSignals(size_norm=1.0)
        weights = ScoreWeights(
            size=1.0, credibility=0, contradiction=0,
            reuse_recency=0, evergreen_recency=0,
        )
        assert compute_score(signals, weights) == 1.0


# ---------------------------------------------------------------------------
# DB rebuild end-to-end
# ---------------------------------------------------------------------------


def _build_seeded_vault(tmp_path: Path) -> tuple[Path, Path]:
    """A 3-community vault: small + medium + large, with one
    community having higher source-credibility and another having
    an open contradiction touching its members.  Designed so the
    final scores produce a clear ordering."""
    vault = tmp_path / "vault"
    vault.mkdir()
    db = vault / "60-Logs" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)

    pack = "research-tech"

    # Build 3 communities of varying sizes.
    communities = [
        # cluster_id, label, members, source_slugs (top-K of members)
        ("cluster::small", "Small topic", ["a"], ["a"]),
        ("cluster::medium", "Medium topic", ["b1", "b2", "b3"], ["b1", "b2", "b3"]),
        ("cluster::large", "Large topic", [f"c{i}" for i in range(20)],
         [f"c{i}" for i in range(8)]),
    ]
    # Seed objects for every member.
    for cid, label, members, slugs in communities:
        for oid in members:
            canonical = f"10-Knowledge/Evergreen/{oid}.md"
            (vault / canonical).parent.mkdir(parents=True, exist_ok=True)
            (vault / canonical).write_text(f"body of {oid}", encoding="utf-8")
            conn.execute(
                "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
                (pack, oid, "evergreen", oid, canonical, f"src-{oid}"),
            )
        conn.execute(
            "INSERT INTO graph_clusters VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack, cid, "louvain_community", label, members[0],
             json.dumps(members), float(len(members))),
        )
        conn.execute(
            "INSERT INTO community_crystals (pack, cluster_id, body_md, "
            "source_evergreen_slugs_json, synthesized_at, llm_model, "
            "prompt_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack, cid, "## body", json.dumps(slugs),
             "2026-05-04T01:00:00.000000+00:00", "m", "v1"),
        )

    # Seed source_authority with high credibility for the medium
    # community's sources only.  This makes medium's
    # credibility_norm = 1.0 and the others = 0.
    for oid in ("b1", "b2", "b3"):
        conn.execute(
            "INSERT INTO source_authority "
            "(source_id, authority, signals_json, scored_at, scorer_version) "
            "VALUES (?, ?, '{}', '', '')",
            (f"src-{oid}", 0.95),
        )

    # An open contradiction whose sources point at the LARGE community.
    conn.execute(
        "INSERT INTO claims VALUES (?, ?, ?, ?, ?, 1.0)",
        (pack, "c0::pp", "c0", "page_summary", "X"),
    )
    conn.execute(
        "INSERT INTO claims VALUES (?, ?, ?, ?, ?, 1.0)",
        (pack, "c1::nn", "c1", "page_summary", "not X"),
    )
    conn.execute(
        "INSERT INTO contradictions VALUES "
        "(?, ?, ?, ?, ?, ?, '', '')",
        (pack, "contradiction::tens", "x",
         json.dumps(["c0::pp"]), json.dumps(["c1::nn"]), "open"),
    )
    conn.commit()
    conn.close()
    return vault, db


class TestRebuildEndToEnd:
    def test_three_communities_score_in_expected_order(self, tmp_path):
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        try:
            scores = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        assert len(scores) == 3
        by_id = {s.crystal_id: s for s in scores}
        # Sanity:
        # - small (1 member, no credibility, no contradiction) → low
        # - medium (3 members, max credibility) → medium-high
        # - large (20 members, hosts the only open contradiction) → high
        assert by_id["cluster::small"].score < by_id["cluster::medium"].score
        assert by_id["cluster::small"].score < by_id["cluster::large"].score
        # Large's contradiction signal contributes (size_norm + contra)
        # while medium leans on credibility.  Both > small.
        assert by_id["cluster::small"].score < 0.30
        assert by_id["cluster::large"].score > 0.40

    def test_signals_persist_to_db(self, tmp_path):
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        try:
            rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT crystal_id, score, size_norm, credibility_norm, "
            "contradiction_norm, evergreen_recency_norm "
            "FROM crystal_scores ORDER BY crystal_id"
        ).fetchall()
        conn.close()
        assert len(rows) == 3
        # Every row's individual signals are persisted alongside the
        # aggregate score so downstream surfaces can render
        # "why this crystal is high-scoring" without recomputing.
        for cid, score, size_n, cred_n, contra_n, recency_n in rows:
            assert 0.0 <= size_n <= 1.0
            assert 0.0 <= cred_n <= 1.0
            assert 0.0 <= contra_n <= 1.0
            assert 0.0 <= recency_n <= 1.0
            assert 0.0 <= score <= 1.0

    def test_rerun_is_idempotent(self, tmp_path):
        # Re-derivability invariant from ARCHITECTURE.md.  The
        # ``evergreen_recency_norm`` signal is intrinsically time-
        # dependent (real seconds elapse between rebuild calls), so
        # we assert byte-identity on the time-independent signals
        # and tolerance on the recency-driven values.
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        try:
            first = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
            second = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        first_by_id = {s.crystal_id: s for s in first}
        second_by_id = {s.crystal_id: s for s in second}
        assert set(first_by_id) == set(second_by_id)
        for cid, s1 in first_by_id.items():
            s2 = second_by_id[cid]
            # Time-independent signals: byte-identical.
            assert s1.signals.size_norm == s2.signals.size_norm
            assert s1.signals.credibility_norm == s2.signals.credibility_norm
            assert s1.signals.contradiction_norm == s2.signals.contradiction_norm
            assert s1.signals.reuse_recency_norm == s2.signals.reuse_recency_norm
            # Recency drifts with elapsed time; tolerance is tight
            # enough to catch real bugs (e.g., wrong time zone
            # arithmetic) but absorbs sub-second test runs.
            assert abs(
                s1.signals.evergreen_recency_norm
                - s2.signals.evergreen_recency_norm
            ) < 1e-3
            assert abs(s1.score - s2.score) < 1e-3

    def test_empty_pack_clears_stale_rows(self, tmp_path):
        # Vault with no community/contradiction crystals: rebuild
        # must clear any existing scores.  Pre-fix a stale row could
        # outlive its source crystal indefinitely.
        vault = tmp_path / "vault"
        vault.mkdir()
        db = vault / "60-Logs" / "knowledge.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA)
        # Pre-seed a stale score row.
        conn.execute(
            "INSERT INTO crystal_scores VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("research-tech", "community", "cluster::stale",
             0.5, 0, 0, 0, 0, 0, "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()

        scores = rebuild_crystal_scores(
            conn, vault_dir=vault, pack="research-tech",
        )
        assert scores == []
        # Stale row was cleared.
        n = conn.execute("SELECT COUNT(*) FROM crystal_scores").fetchone()[0]
        conn.close()
        assert n == 0

    def test_missing_source_authority_table_degrades_gracefully(self, tmp_path):
        # A fresh vault that hasn't run ovp-score-sources yet has no
        # source_authority table.  Scoring must proceed with
        # credibility_norm = 0 across the board, never crash.
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE source_authority")
        conn.commit()
        try:
            scores = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        assert len(scores) == 3
        # All credibility_norm = 0 because no table → no signal.
        for s in scores:
            assert s.signals.credibility_norm == 0.0


class TestReuseFeedbackLoop:
    """BL-049: the ``reuse_recency_norm`` signal reads from the
    ``reuse_events`` table.  Before this PR it was a forward-compat
    placeholder fixed at 0; now it actually scales with how often
    each crystal has been touched in the rolling 30-day window."""

    def test_cold_start_signal_is_zero(self, tmp_path):
        # No reuse_events rows → all crystals get reuse_recency_norm = 0.
        # Same behaviour as the BL-045 v0 placeholder.
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        try:
            scores = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        for s in scores:
            assert s.signals.reuse_recency_norm == 0.0

    def test_in_window_event_lifts_signal(self, tmp_path):
        # Seed a reuse_events row for one community within the
        # 30-day window.  That crystal's reuse_recency_norm becomes
        # 1.0 (the per-pack max with only one event); others stay 0.
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        # Pick a real cluster_id from the seed.
        cluster_id = conn.execute(
            "SELECT cluster_id FROM community_crystals LIMIT 1"
        ).fetchone()[0]
        # ts within 30 days — use today.
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO reuse_events "
            "(event_id, ts, pack, object_id, object_kind, surface) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("e1", now_iso, "research-tech", cluster_id,
             "community_crystal", "atlas"),
        )
        conn.commit()
        try:
            scores = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        by_id = {s.crystal_id: s for s in scores}
        # The reused crystal lifts its signal to 1.0.
        assert by_id[cluster_id].signals.reuse_recency_norm == 1.0
        # Other crystals stay at zero.
        for cid, s in by_id.items():
            if cid == cluster_id:
                continue
            assert s.signals.reuse_recency_norm == 0.0

    def test_old_event_outside_window_ignored(self, tmp_path):
        # Event older than 30 days → doesn't count.
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        cluster_id = conn.execute(
            "SELECT cluster_id FROM community_crystals LIMIT 1"
        ).fetchone()[0]
        from datetime import datetime, timedelta, timezone
        old_iso = (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO reuse_events "
            "(event_id, ts, pack, object_id, object_kind, surface) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("eOld", old_iso, "research-tech", cluster_id,
             "community_crystal", "atlas"),
        )
        conn.commit()
        try:
            scores = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        # 60-day-old event is outside the 30-day window → signal stays cold.
        for s in scores:
            assert s.signals.reuse_recency_norm == 0.0

    def test_other_pack_events_dont_leak(self, tmp_path):
        # Pack isolation: a reuse event in another pack must not
        # affect this pack's crystal scores.
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        cluster_id = conn.execute(
            "SELECT cluster_id FROM community_crystals LIMIT 1"
        ).fetchone()[0]
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO reuse_events "
            "(event_id, ts, pack, object_id, object_kind, surface) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("eOther", now_iso, "other-pack", cluster_id,
             "community_crystal", "atlas"),
        )
        conn.commit()
        try:
            scores = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        for s in scores:
            assert s.signals.reuse_recency_norm == 0.0

    def test_missing_reuse_events_table_degrades_gracefully(self, tmp_path):
        # Fresh DB without reuse_events → no crash; signal cold-starts at 0.
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE reuse_events")
        conn.commit()
        try:
            scores = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        # Still produces scores; reuse signal is 0.
        assert len(scores) == 3
        for s in scores:
            assert s.signals.reuse_recency_norm == 0.0


class TestArchitectureBoundary:
    """The ARCHITECTURE.md template invariant for a Projection: it
    can be deleted and rebuilt; it never writes Canonical State.
    These tests pin both halves."""

    def test_rebuild_does_not_modify_canonical_state(self, tmp_path):
        vault, db = _build_seeded_vault(tmp_path)
        # Snapshot Canonical-State-adjacent tables before/after.
        conn = sqlite3.connect(db)
        before = {
            "objects": conn.execute(
                "SELECT * FROM objects ORDER BY object_id"
            ).fetchall(),
            "claims": conn.execute(
                "SELECT * FROM claims ORDER BY claim_id"
            ).fetchall(),
            "graph_clusters": conn.execute(
                "SELECT * FROM graph_clusters ORDER BY cluster_id"
            ).fetchall(),
            "contradictions": conn.execute(
                "SELECT * FROM contradictions ORDER BY contradiction_id"
            ).fetchall(),
        }
        rebuild_crystal_scores(
            conn, vault_dir=vault, pack="research-tech",
        )
        after = {
            "objects": conn.execute(
                "SELECT * FROM objects ORDER BY object_id"
            ).fetchall(),
            "claims": conn.execute(
                "SELECT * FROM claims ORDER BY claim_id"
            ).fetchall(),
            "graph_clusters": conn.execute(
                "SELECT * FROM graph_clusters ORDER BY cluster_id"
            ).fetchall(),
            "contradictions": conn.execute(
                "SELECT * FROM contradictions ORDER BY contradiction_id"
            ).fetchall(),
        }
        conn.close()
        # Every Canonical-State-adjacent table is byte-identical.
        for table, before_rows in before.items():
            assert before_rows == after[table], (
                f"crystal scoring should not have touched {table!r}"
            )

    def test_drop_and_rebuild_recovers_identical_state(self, tmp_path):
        # The Projection invariant: drop the table → rebuild from
        # upstream → state recovers.  Tests the deepest contract.
        # Same time-independence note as TestRebuildEndToEnd.test_rerun.
        vault, db = _build_seeded_vault(tmp_path)
        conn = sqlite3.connect(db)
        try:
            first = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
            # Drop the projection.
            conn.execute("DELETE FROM crystal_scores")
            conn.commit()
            assert conn.execute(
                "SELECT COUNT(*) FROM crystal_scores"
            ).fetchone()[0] == 0
            # Rebuild from scratch.
            second = rebuild_crystal_scores(
                conn, vault_dir=vault, pack="research-tech",
            )
        finally:
            conn.close()
        first_by_id = {s.crystal_id: s for s in first}
        second_by_id = {s.crystal_id: s for s in second}
        assert set(first_by_id) == set(second_by_id)
        for cid, s1 in first_by_id.items():
            s2 = second_by_id[cid]
            # Time-independent signals are byte-identical.
            assert s1.signals.size_norm == s2.signals.size_norm
            assert s1.signals.credibility_norm == s2.signals.credibility_norm
            assert s1.signals.contradiction_norm == s2.signals.contradiction_norm
            # Score within tolerance (only recency drifts).
            assert abs(s1.score - s2.score) < 1e-3
