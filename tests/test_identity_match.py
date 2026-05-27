"""BL-115 / BL-116 — Jaccard concept-identity matcher + orphan supersede.

The matcher decides, after every re-cluster:

* which prior concepts INHERIT a new cluster_id (Jaccard >= 0.6)
* which new clusters get a freshly-minted concept_id (no prior match)
* which prior concepts are ORPHANED (no inheritor at threshold)

BL-116 then supersedes the orphans' active crystals in the same
transaction.  These tests pin the contract end-to-end on a synthetic
DB so the algorithm is locked before it lands on the operator vault.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ovp_pipeline.knowledge_index import SCHEMA
from ovp_pipeline.synthesis.identity_match import (
    DEFAULT_JACCARD_THRESHOLD,
    match_concept_identities,
)


PACK = "research-tech"


def _new_db(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "knowledge.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    return conn


def _seed_concept(
    conn: sqlite3.Connection, *, concept_id: str, cluster_id: str,
    synth_at: str = "2026-05-26T00:00:00.000000+00:00",
) -> None:
    """Insert one active community_crystal — the trigger seeds the
    matching ledger row automatically (BL-114 contract)."""
    conn.execute(
        "INSERT INTO community_crystals "
        "(pack, cluster_id, body_md, source_evergreen_slugs_json,"
        " synthesized_at, llm_model, prompt_version, concept_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (PACK, cluster_id, "body", "[]", synth_at, "m", "v1", concept_id),
    )


# ── Pure Jaccard primitives ──────────────────────────────────────


def test_perfect_overlap_inherits_identity(tmp_path: Path):
    """Prior cluster_id = new cluster_id (same members → same sha1)
    is the no-op case: matcher should NOT churn the ledger or emit
    events."""
    conn = _new_db(tmp_path)
    try:
        _seed_concept(conn, concept_id="c1", cluster_id="cluster::aa")
        result = match_concept_identities(
            conn, pack=PACK,
            prior_clusters={"cluster::aa": ["a", "b", "c"]},
            new_clusters={"cluster::aa": ["a", "b", "c"]},
            now_ts="2026-05-26T01:00:00+00:00",
        )
        # No-op match → no inherited event, no created event, no orphan.
        assert result.inherited == []
        assert result.created == []
        assert result.orphaned == []
        assert result.superseded_crystals == 0
    finally:
        conn.close()


def test_single_member_swap_preserves_identity(tmp_path: Path):
    """10-member cluster with one member added: Jaccard 10/11 ≈ 0.91
    > 0.6 → identity inherited.  Ledger.current_cluster_id updates
    to the new id; lineage_json records the chain."""
    conn = _new_db(tmp_path)
    try:
        _seed_concept(conn, concept_id="cluster::v1", cluster_id="cluster::v1")
        prior = {f"m{i}" for i in range(10)}
        new = prior | {"m_new"}
        result = match_concept_identities(
            conn, pack=PACK,
            prior_clusters={"cluster::v1": sorted(prior)},
            new_clusters={"cluster::v2": sorted(new)},
            now_ts="2026-05-26T01:00:00+00:00",
        )
        assert len(result.inherited) == 1
        ev = result.inherited[0]
        assert ev["concept_id"] == "cluster::v1"
        assert ev["from_cluster_id"] == "cluster::v1"
        assert ev["to_cluster_id"] == "cluster::v2"
        assert ev["jaccard"] >= 0.6
        # Ledger updated.
        row = conn.execute(
            "SELECT current_cluster_id, lineage_json "
            "FROM concept_identity_ledger WHERE concept_id = 'cluster::v1'"
        ).fetchone()
        assert row[0] == "cluster::v2"
        lineage = json.loads(row[1])
        assert lineage == [{
            "from_cluster_id": "cluster::v1",
            "to_cluster_id": "cluster::v2",
            "at": "2026-05-26T01:00:00+00:00",
        }]
        # No orphans, no created.
        assert result.orphaned == []
        assert result.created == []
    finally:
        conn.close()


def test_majority_swap_orphans_prior_and_creates_new(tmp_path: Path):
    """6-of-10 members swap: Jaccard 4/14 ≈ 0.29 < 0.6 → prior concept
    orphaned, new concept minted, BL-116 supersedes the prior
    crystals."""
    conn = _new_db(tmp_path)
    try:
        _seed_concept(conn, concept_id="cluster::old", cluster_id="cluster::old")
        prior = {f"keep{i}" for i in range(4)} | {f"gone{i}" for i in range(6)}
        new = {f"keep{i}" for i in range(4)} | {f"fresh{i}" for i in range(10)}
        result = match_concept_identities(
            conn, pack=PACK,
            prior_clusters={"cluster::old": sorted(prior)},
            new_clusters={"cluster::new": sorted(new)},
            now_ts="2026-05-26T01:00:00+00:00",
        )
        assert result.inherited == []
        # Prior concept orphaned.
        assert len(result.orphaned) == 1
        orphan = result.orphaned[0]
        assert orphan["concept_id"] == "cluster::old"
        assert orphan["prior_cluster_id"] == "cluster::old"
        assert orphan["superseded_count"] == 1
        # New concept created.
        assert len(result.created) == 1
        assert result.created[0]["concept_id"] == "cluster::new"
        assert result.created[0]["member_count"] == 14
        # BL-116: prior crystals are now superseded with the right
        # reason — read paths joining via ledger no longer return
        # them as active.
        row = conn.execute(
            "SELECT superseded_by_synthesized_at, supersede_reason "
            "FROM community_crystals WHERE concept_id = 'cluster::old'"
        ).fetchone()
        assert row[0] == "2026-05-26T01:00:00+00:00"
        assert row[1] == "orphaned_by_reclustering"
    finally:
        conn.close()


# ── Greedy assignment fidelity ────────────────────────────────────


def test_greedy_assigns_highest_jaccard_first(tmp_path: Path):
    """Three prior concepts, three new clusters, partially overlapping:
    greedy must pair the highest-Jaccard pair first, then the second-
    highest from the residuals, even when a globally-suboptimal
    assignment looks tempting locally."""
    conn = _new_db(tmp_path)
    try:
        for cid in ("c_alpha", "c_beta", "c_gamma"):
            _seed_concept(conn, concept_id=cid, cluster_id=cid)
        # Designed so:
        #   alpha → A: Jaccard 10/10 = 1.0  (perfect)
        #   alpha → B: Jaccard 9/11  = 0.82
        #   beta  → B: Jaccard 8/12  = 0.67
        #   gamma → C: Jaccard 6/14  = 0.43 (< threshold)
        # Greedy: alpha→A first, beta→B, gamma orphaned (C unmatched
        # below threshold → created).
        prior = {
            "c_alpha": [f"x{i}" for i in range(10)],
            "c_beta":  ["x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7", "y0", "y1"],
            "c_gamma": [f"g{i}" for i in range(6)],
        }
        new = {
            "A": [f"x{i}" for i in range(10)],
            "B": [f"x{i}" for i in range(8)] + ["y0", "y1", "y2", "y3"],
            "C": [f"z{i}" for i in range(8)],
        }
        result = match_concept_identities(
            conn, pack=PACK,
            prior_clusters=prior, new_clusters=new,
            now_ts="2026-05-26T01:00:00+00:00",
        )
        inh = {ev["concept_id"]: ev["to_cluster_id"] for ev in result.inherited}
        assert inh.get("c_alpha") == "A"
        assert inh.get("c_beta") == "B"
        assert "c_gamma" not in inh  # below threshold
        # Gamma orphaned, C created.
        orph_ids = {ev["concept_id"] for ev in result.orphaned}
        assert "c_gamma" in orph_ids
        created_ids = {ev["concept_id"] for ev in result.created}
        assert "C" in created_ids
    finally:
        conn.close()


def test_below_threshold_pair_does_not_inherit(tmp_path: Path):
    """A new cluster whose best Jaccard is just under threshold must
    NOT inherit — it gets a fresh concept_id and the prior is
    orphaned independently."""
    conn = _new_db(tmp_path)
    try:
        _seed_concept(conn, concept_id="c_old", cluster_id="c_old")
        # Jaccard 3/10 = 0.3 — well under 0.6.
        prior = {"a", "b", "c", "d", "e", "f", "g"}
        new = {"a", "b", "c", "x", "y", "z"}
        result = match_concept_identities(
            conn, pack=PACK,
            prior_clusters={"c_old": sorted(prior)},
            new_clusters={"c_new": sorted(new)},
            now_ts="2026-05-26T01:00:00+00:00",
        )
        assert result.inherited == []
        assert len(result.created) == 1
        assert len(result.orphaned) == 1
    finally:
        conn.close()


# ── Idempotency ───────────────────────────────────────────────────


def test_second_invocation_with_identical_input_is_noop(tmp_path: Path):
    """Re-running on identical input produces zero new events and
    leaves the ledger byte-identical — the "quiet vault" guarantee."""
    conn = _new_db(tmp_path)
    try:
        _seed_concept(conn, concept_id="c1", cluster_id="cluster::aa")
        clusters = {"cluster::aa": ["a", "b", "c"]}
        # First run is also a no-op (same cluster_id either side).
        r1 = match_concept_identities(
            conn, pack=PACK, prior_clusters=clusters, new_clusters=clusters,
            now_ts="2026-05-26T01:00:00+00:00",
        )
        ledger_after_1 = conn.execute(
            "SELECT * FROM concept_identity_ledger"
        ).fetchall()
        r2 = match_concept_identities(
            conn, pack=PACK, prior_clusters=clusters, new_clusters=clusters,
            now_ts="2026-05-26T02:00:00+00:00",
        )
        ledger_after_2 = conn.execute(
            "SELECT * FROM concept_identity_ledger"
        ).fetchall()
        assert ledger_after_1 == ledger_after_2
        assert r1.inherited == r2.inherited == []
        assert r1.created == r2.created == []
        assert r1.orphaned == r2.orphaned == []
    finally:
        conn.close()


# ── BL-116 supersede integration ──────────────────────────────────


def test_supersede_only_touches_active_crystals(tmp_path: Path):
    """BL-116 must not re-supersede crystals that are already
    superseded (e.g. from a prior re-synthesis).  The UPDATE filters
    on superseded_by_synthesized_at = '' for that reason."""
    conn = _new_db(tmp_path)
    try:
        # Two crystal versions for the same concept: v1 already
        # superseded, v2 active.
        conn.execute(
            "INSERT INTO community_crystals "
            "(pack, cluster_id, body_md, source_evergreen_slugs_json,"
            " synthesized_at, llm_model, prompt_version, concept_id,"
            " superseded_by_synthesized_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (PACK, "old", "v1", "[]", "2026-05-20T00:00:00+00:00",
             "m", "v1", "old", "2026-05-25T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO community_crystals "
            "(pack, cluster_id, body_md, source_evergreen_slugs_json,"
            " synthesized_at, llm_model, prompt_version, concept_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (PACK, "old", "v2", "[]", "2026-05-25T00:00:00+00:00",
             "m", "v1", "old"),
        )
        # Force the concept into orphan territory.
        result = match_concept_identities(
            conn, pack=PACK,
            prior_clusters={"old": ["m1", "m2", "m3"]},
            new_clusters={"new": ["zzz1", "zzz2", "zzz3"]},
            now_ts="2026-05-26T01:00:00+00:00",
        )
        assert len(result.orphaned) == 1
        # Only the active row got superseded; the already-superseded
        # row keeps its prior supersede_by stamp.
        rows = conn.execute(
            "SELECT body_md, superseded_by_synthesized_at, supersede_reason "
            "FROM community_crystals WHERE concept_id = 'old' "
            "ORDER BY synthesized_at"
        ).fetchall()
        assert rows[0] == ("v1", "2026-05-25T00:00:00+00:00", "")
        assert rows[1] == ("v2", "2026-05-26T01:00:00+00:00",
                           "orphaned_by_reclustering")
    finally:
        conn.close()


# ── Edge cases ────────────────────────────────────────────────────


def test_empty_prior_skips_match(tmp_path: Path):
    """Fresh vault / first rebuild — no prior concepts.  Every new
    cluster gets created; nothing orphans."""
    conn = _new_db(tmp_path)
    try:
        result = match_concept_identities(
            conn, pack=PACK,
            prior_clusters={},
            new_clusters={"c1": ["a", "b"], "c2": ["x", "y"]},
            now_ts="2026-05-26T01:00:00+00:00",
        )
        assert result.inherited == []
        assert {ev["concept_id"] for ev in result.created} == {"c1", "c2"}
        assert result.orphaned == []
    finally:
        conn.close()


def test_threshold_is_inclusive(tmp_path: Path):
    """Jaccard exactly at the threshold counts as inherit, not orphan
    — the spec says ``>= 0.6``."""
    conn = _new_db(tmp_path)
    try:
        _seed_concept(conn, concept_id="c", cluster_id="c")
        # Jaccard = 3/5 = 0.6 exactly.
        result = match_concept_identities(
            conn, pack=PACK,
            prior_clusters={"c": ["a", "b", "c", "d"]},
            new_clusters={"c_new": ["a", "b", "c", "e"]},
            now_ts="2026-05-26T01:00:00+00:00",
            threshold=DEFAULT_JACCARD_THRESHOLD,
        )
        # 3 common, 5 unique → 3/5 = 0.6 — inherits, not orphans.
        assert len(result.inherited) == 1
        assert result.orphaned == []
    finally:
        conn.close()
