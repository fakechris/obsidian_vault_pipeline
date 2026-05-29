"""BL-115 / BL-116 — concept-identity continuity across re-clusters.

The problem BL-114 left dangling
================================

BL-114 added a stable ``concept_id`` column on ``community_crystals``
and a ``concept_identity_ledger`` mapping each concept to its
*current* Louvain ``cluster_id``.  At seed time concept_id ==
cluster_id and the ledger is a 1:1 mirror.

But ``knowledge_index`` rebuilds ``graph_clusters`` from scratch on
every run — Louvain re-runs over the new edge set, and cluster_ids
(``sha1(sorted(members))``) shift whenever membership changes.  Pre-
BL-115 the ledger stayed pinned to the OLD cluster_ids; the read-path
joins through ``cil.current_cluster_id`` therefore silently dropped
every concept whose cluster shifted (135 / 578 on the operator vault).

What BL-115 does
================

After every ``graph_clusters`` rebuild this module compares the prior
member sets (captured before the truncate) against the new ones via
**Jaccard similarity**.  Greedy bipartite assignment:

  1. Compute Jaccard for every (prior_concept_id, new_cluster_id) pair.
  2. Sort pairs by descending overlap, tie-broken by (larger member
     count, alphabetic cluster_id).
  3. Walk the sorted list, assigning first-fit: skip pairs where the
     concept or cluster has already been claimed.
  4. Overlap >= ``threshold`` (default 0.6) → the new cluster
     *inherits* the prior concept_id (ledger row's
     ``current_cluster_id`` is updated to the new cluster_id;
     lineage_json records the old cluster_id).
  5. New clusters with no qualifying match get a fresh concept_id
     (we reuse ``cluster_id`` — already a content hash — so concept_id
     stays stable across no-op re-runs).
  6. Prior concepts with no qualifying match are *orphans*.  BL-116
     supersedes their active crystals in the same transaction.

Why 0.6
=======

A 10-member cluster losing 1 / gaining 1 member yields Jaccard 9/11
= 0.82 (well above threshold — same concept).  Losing 6 / gaining 4
yields 4/14 = 0.28 (below — different concept).  0.6 is the natural
midpoint where ~half the members must overlap, which empirically
matches what operators consider "the same topic".

Idempotency
===========

If nothing changed since the last rebuild — prior and new cluster
sets are identical — every match is a perfect self-pair, every
ledger row already points at the right cluster_id, and the supersede
loop has no orphans to act on.  Re-running ``knowledge_index`` on a
quiet vault produces zero ledger UPDATEs and zero ``concept_identity_*``
audit events.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# Jaccard threshold for identity inheritance.  Documented in the
# module docstring and BL-115 spec; tuning lives here.
DEFAULT_JACCARD_THRESHOLD = 0.6


@dataclass(frozen=True)
class MatchResult:
    """Outcome of a single ``match_concept_identities`` invocation.

    Each field is a list of records the caller can emit as audit
    events.  ``orphaned`` is the input BL-116 acts on — every concept
    in here has its active crystals superseded in the same transaction
    that produced this result.
    """

    inherited: list[dict] = field(default_factory=list)
    created: list[dict] = field(default_factory=list)
    orphaned: list[dict] = field(default_factory=list)
    superseded_crystals: int = 0


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _load_prior_ledger(
    conn: sqlite3.Connection, pack: str,
) -> dict[str, str]:
    """Return ``{concept_id: current_cluster_id}`` for every concept
    in the ledger for this pack — BL-114 seeded one entry per active
    community_crystal, so on first re-cluster after BL-114 ships
    this returns ``cluster_id → cluster_id`` for every concept."""
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT concept_id, current_cluster_id "
            "  FROM concept_identity_ledger "
            " WHERE pack = ?",
            (pack,),
        )
    }


def _greedy_assign(
    prior_members: dict[str, set[str]],
    new_members: dict[str, set[str]],
    threshold: float,
) -> tuple[dict[str, str], set[str], set[str]]:
    """Greedy bipartite Jaccard match.

    Returns ``(assignment, claimed_concepts, claimed_clusters)`` where
    ``assignment[new_cluster_id] = prior_concept_id`` for every pair
    that crossed the threshold and survived first-fit.  The two sets
    are the unmatched residuals (caller mints fresh concept_ids for
    unmatched clusters and orphans the unmatched concepts).
    """
    # Build all candidate pairs with Jaccard >= threshold.  Lower-bound
    # filter here so the sort stays cheap even on packs with thousands
    # of clusters — the prior implementation's biggest cost was the
    # full O(N*M) cartesian without an early skip.
    candidates: list[tuple[float, int, str, str, str]] = []
    for concept_id, p_set in prior_members.items():
        for cluster_id, n_set in new_members.items():
            j = _jaccard(p_set, n_set)
            if j < threshold:
                continue
            # Tie-break key bundle so the sort is fully deterministic
            # without a stable-sort dependency: (jaccard desc, larger
            # member count desc, alphabetic cluster_id asc).  Negate
            # the first two so ``sorted`` ascending gives the desired
            # ordering.
            tie = max(len(p_set), len(n_set))
            candidates.append((-j, -tie, cluster_id, concept_id, cluster_id))
    candidates.sort()

    assignment: dict[str, str] = {}
    claimed_concepts: set[str] = set()
    claimed_clusters: set[str] = set()
    for _neg_j, _neg_tie, _tie_key, concept_id, cluster_id in candidates:
        if concept_id in claimed_concepts or cluster_id in claimed_clusters:
            continue
        assignment[cluster_id] = concept_id
        claimed_concepts.add(concept_id)
        claimed_clusters.add(cluster_id)
    return assignment, claimed_concepts, claimed_clusters


def match_concept_identities(
    conn: sqlite3.Connection,
    *,
    pack: str,
    prior_clusters: dict[str, list[str]],
    new_clusters: dict[str, list[str]],
    now_ts: str,
    threshold: float = DEFAULT_JACCARD_THRESHOLD,
) -> MatchResult:
    """Match prior concepts to newly-rebuilt clusters via Jaccard.

    Caller responsibilities:

    * ``prior_clusters``: snapshot of ``graph_clusters.member_object_ids_json``
      taken BEFORE the truncate, keyed by ``cluster_id``.
    * ``new_clusters``: the freshly-inserted ``graph_clusters`` rows
      this rebuild produced, keyed by ``cluster_id``.
    * ``now_ts``: ISO timestamp used for ``last_matched_at`` on
      inherited rows AND ``superseded_by_synthesized_at`` on orphan
      crystals — passed in so the same wall-clock value flows through
      every write in this transaction.

    Side effects on the connection:

    * Updates ``concept_identity_ledger.current_cluster_id`` +
      ``last_matched_at`` for each inherited concept (BL-115).
    * Inserts new ledger rows for unmatched new clusters (BL-115).
    * For each orphaned concept, supersedes every active
      ``community_crystals`` row with ``supersede_reason =
      'orphaned_by_reclustering'`` (BL-116).

    The caller commits.  Returns the events for downstream audit emit.
    """
    if not prior_clusters and not new_clusters:
        return MatchResult()

    prior_ledger = _load_prior_ledger(conn, pack)
    # Translate the prior cluster snapshot into "per concept_id" sets
    # via the ledger.  Concepts whose current_cluster_id no longer
    # exists in the prior snapshot get an empty member set — they
    # can't match anything, so they go straight to orphans.
    prior_member_sets: dict[str, set[str]] = {}
    for concept_id, current_cluster_id in prior_ledger.items():
        members = prior_clusters.get(current_cluster_id, [])
        prior_member_sets[concept_id] = set(members)

    new_member_sets: dict[str, set[str]] = {
        cluster_id: set(members) for cluster_id, members in new_clusters.items()
    }

    assignment, claimed_concepts, claimed_clusters = _greedy_assign(
        prior_member_sets, new_member_sets, threshold,
    )

    inherited: list[dict] = []
    created: list[dict] = []
    orphaned: list[dict] = []

    # 1. Inherited — update ledger to point at the new cluster_id.
    for new_cluster_id, concept_id in assignment.items():
        prior_cluster_id = prior_ledger[concept_id]
        if prior_cluster_id == new_cluster_id:
            # No change — the cluster kept its id (member set is
            # close enough that sha1 happened to land on the same
            # hash).  Skip the UPDATE so idempotent runs don't churn
            # ``last_matched_at`` for nothing.
            continue
        # Append the prior cluster_id to lineage_json so the chain
        # is reconstructible.  Read-modify-write — fine in a small
        # ledger; if this ever scales past tens of thousands we
        # switch to a separate ``concept_identity_lineage`` table.
        row = conn.execute(
            "SELECT lineage_json FROM concept_identity_ledger "
            " WHERE pack = ? AND concept_id = ?",
            (pack, concept_id),
        ).fetchone()
        try:
            lineage = json.loads(row[0]) if row and row[0] else []
        except (TypeError, json.JSONDecodeError):
            lineage = []
        if not isinstance(lineage, list):
            lineage = []
        lineage.append({
            "from_cluster_id": prior_cluster_id,
            "to_cluster_id": new_cluster_id,
            "at": now_ts,
        })
        conn.execute(
            "UPDATE concept_identity_ledger "
            "   SET current_cluster_id = ?,"
            "       last_matched_at    = ?,"
            "       lineage_json       = ?"
            " WHERE pack = ? AND concept_id = ?",
            (new_cluster_id, now_ts, json.dumps(lineage), pack, concept_id),
        )
        inherited.append({
            "concept_id": concept_id,
            "from_cluster_id": prior_cluster_id,
            "to_cluster_id": new_cluster_id,
            "jaccard": round(_jaccard(
                prior_member_sets[concept_id],
                new_member_sets[new_cluster_id],
            ), 4),
        })

    # 2. Created — every new cluster that didn't inherit gets a fresh
    # concept_id.  We reuse the cluster_id itself: at seed it's
    # sha1(sorted members), so it's deterministic and won't collide
    # with anything that hashes differently.  The trigger from BL-114
    # would have written this row on the next community_crystal
    # INSERT, but we want the ledger consistent right now so reads
    # work between identity-match and the next synthesize cycle.
    for cluster_id, members in new_clusters.items():
        if cluster_id in claimed_clusters:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO concept_identity_ledger "
            "    (pack, concept_id, current_cluster_id,"
            "     last_matched_at, created_at, lineage_json) "
            "VALUES (?, ?, ?, ?, ?, '[]')",
            (pack, cluster_id, cluster_id, now_ts, now_ts),
        )
        created.append({
            "concept_id": cluster_id,
            "cluster_id": cluster_id,
            "member_count": len(members),
        })

    # 3. Orphaned — every prior concept with no inheritor.  BL-116
    # supersedes its active crystals in the same transaction.
    #
    # ``no_prior_member_signal`` orphans (concept_id whose
    # ``current_cluster_id`` had no row in the prior graph_clusters
    # snapshot) are STILL real orphans — that's exactly the BL-114
    # data-integrity hole we set out to close, where the ledger row
    # exists but graph_clusters lost its corresponding cluster.  We
    # supersede them too; the ``WHERE superseded_by_synthesized_at
    # = ''`` filter is what keeps already-superseded crystals from
    # getting a fresh supersede stamp.
    superseded_crystals = 0
    for concept_id in prior_ledger:
        if concept_id in claimed_concepts:
            continue
        prior_set = prior_member_sets.get(concept_id) or set()
        reason = (
            "no_matching_new_cluster" if prior_set
            else "no_prior_member_signal"
        )
        cur = conn.execute(
            "UPDATE community_crystals "
            "   SET superseded_by_synthesized_at = ?,"
            "       supersede_reason             = 'orphaned_by_reclustering'"
            " WHERE pack = ?"
            "   AND concept_id = ?"
            "   AND superseded_by_synthesized_at = ''",
            (now_ts, pack, concept_id),
        )
        n = cur.rowcount or 0
        superseded_crystals += n
        orphaned.append({
            "concept_id": concept_id,
            "prior_cluster_id": prior_ledger[concept_id],
            "reason": reason,
            "superseded_count": n,
        })

    if inherited or created or orphaned:
        logger.info(
            "concept identity match: %d inherited, %d created, %d orphaned "
            "(%d crystals superseded)",
            len(inherited), len(created), len(orphaned), superseded_crystals,
        )

    return MatchResult(
        inherited=inherited,
        created=created,
        orphaned=orphaned,
        superseded_crystals=superseded_crystals,
    )


def snapshot_prior_graph_clusters(
    db_path,
    *,
    pack: str,
) -> dict[str, list[str]]:
    """Read the prior ``graph_clusters`` set for ``pack`` from
    ``db_path`` and return ``{cluster_id: members}``.

    Empty dict when the DB doesn't exist (first rebuild on a fresh
    vault) or the table is missing — ``match_concept_identities``
    treats absence as "no prior signal" and skips inheritance.
    Malformed JSON rows are dropped silently; the affected concept
    will simply orphan on the next match cycle, which is the
    correct behaviour for unrecoverable data.
    """
    out: dict[str, list[str]] = {}
    from pathlib import Path
    p = Path(db_path)
    if not p.exists():
        return out
    try:
        conn = sqlite3.connect(p)
    except sqlite3.DatabaseError:
        return out
    try:
        for cluster_id, members_json in conn.execute(
            "SELECT cluster_id, member_object_ids_json "
            "  FROM graph_clusters "
            " WHERE pack = ? AND cluster_kind = 'louvain_community'",
            (pack,),
        ):
            try:
                out[cluster_id] = list(json.loads(members_json))
            except (TypeError, json.JSONDecodeError):
                continue
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return out


def emit_identity_audit(
    vault_dir,
    *,
    pack: str,
    result: MatchResult,
) -> None:
    """Best-effort audit emit for a ``MatchResult``.

    Canonical state already landed in the ledger + crystals tables;
    a logging failure must not roll back the rebuild.  Called by
    ``knowledge_index.rebuild_knowledge_index`` immediately after
    ``match_concept_identities`` so each event carries the rebuild's
    transaction context.
    """
    try:
        from ..event_emitter import emit as _emit_audit

        for ev in result.inherited:
            _emit_audit(
                vault_dir, "pipeline.jsonl",
                "concept_identity_resolved", ev, pack=pack,
            )
        for ev in result.created:
            _emit_audit(
                vault_dir, "pipeline.jsonl",
                "concept_identity_created", ev, pack=pack,
            )
        for ev in result.orphaned:
            _emit_audit(
                vault_dir, "pipeline.jsonl",
                "concept_identity_orphaned", ev, pack=pack,
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "concept_identity audit emit failed — "
            "ledger/supersede state already committed",
        )


__all__ = [
    "DEFAULT_JACCARD_THRESHOLD",
    "MatchResult",
    "match_concept_identities",
    "snapshot_prior_graph_clusters",
    "emit_identity_audit",
]
