"""BL-117 — Stale crystal detection for budgeted delta synthesis.

After BL-115/116 made identity continuity work, the next gap is
freshness.  A crystal that *kept* its concept_id across a re-cluster
might still have a body_md that's wildly out of sync with the current
member set (think: synthesized when the cluster was 8 evergreens,
now grown to 25).  This module tells the operator (and the
``ovp-resynth-stale-crystals`` CLI) which concepts need re-synthesis,
ranked by signal priority so a budget-capped run hits the worst
first.

Four staleness signals, in priority order:

  (a) ``jaccard_drift`` — Jaccard(synthesis-time slugs, current
      cluster members) < 0.8.  The body was generated from a member
      set that's now substantially different.  Highest priority
      because it's the symptom that most directly produces "the
      crystal says X but the cluster shows Y".

  (b) ``open_contradiction`` — at least one open contradiction touches
      a member of the current cluster, AND that contradiction landed
      after this concept's last synthesis.  The crystal's body
      doesn't reflect the new tension and operators will read it as
      out-of-date.

  (c) ``member_delta`` — current cluster size differs from the
      synthesis-time slug count by >= 5 absolute OR >= 20% of the
      current size.  Less severe than jaccard_drift (the cluster
      grew but stayed coherent) but worth refreshing.

  (d) ``age`` — synthesized_at older than 14 days.  Sentinel for
      crystals that never tripped a > signal but have been sitting
      stale long enough to be worth a refresh on principle.

Idempotency: on a quiet vault (no membership changes, no new
contradictions, age < threshold) the function returns ``[]`` so the
nightly cron does zero LLM work.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


# Tuning knobs — kept module-level for easy override in tests / packs.
JACCARD_STALENESS_THRESHOLD = 0.8
MEMBER_DELTA_ABS = 5
MEMBER_DELTA_RATIO = 0.20
AGE_DAYS_SENTINEL = 14

# Signal priorities.  Lower number = higher priority — the ranker
# uses this as the primary sort key so a budget-capped run hits the
# most-urgent concepts first.
_SIGNAL_PRIORITY = {
    "jaccard_drift": 0,
    "open_contradiction": 1,
    "member_delta": 2,
    "age": 3,
}


@dataclass(frozen=True)
class StaleConcept:
    """One stale community concept that needs re-synthesis.

    Multiple signals can fire for the same concept; ``signals`` is
    the union, ``primary_signal`` is the highest-priority one (used
    for ranking).
    """

    pack: str
    concept_id: str
    current_cluster_id: str
    synthesized_at: str
    signals: tuple[str, ...]
    primary_signal: str
    jaccard: float | None
    member_count_now: int
    member_count_at_synth: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> datetime | None:
    """Best-effort ISO-8601 parse — returns ``None`` on bad input
    so callers can treat it as "definitely stale" via the age branch."""
    if not ts:
        return None
    try:
        # ``fromisoformat`` accepts ``2026-05-26T00:00:00.000000+00:00``;
        # strip trailing Z which it can't parse pre-3.11.
        clean = ts.rstrip("Z")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def compute_crystal_staleness(
    conn: sqlite3.Connection,
    *,
    pack: str,
    now: datetime | None = None,
) -> list[StaleConcept]:
    """Scan the pack's active community concepts and return the stale set.

    Reads from: ``community_crystals`` (active rows only),
    ``concept_identity_ledger`` (current_cluster_id), ``graph_clusters``
    (current members), ``contradictions`` (open status + created_at).

    ``now`` is injectable so tests can pin the age branch without
    monkeypatching ``datetime.now``.  Production callers pass ``None``.

    Results are sorted by (primary_signal_priority asc, jaccard asc,
    synthesized_at asc) so the worst-fresh first.  Callers slice
    ``[:budget]`` to bound LLM spend.
    """
    now = now or _utc_now()
    age_cutoff = now - timedelta(days=AGE_DAYS_SENTINEL)
    age_cutoff_iso = age_cutoff.isoformat(timespec="microseconds")

    # One pass over the active crystals + ledger + clusters.  LEFT
    # JOIN on graph_clusters so concepts with no current cluster
    # surface as "missing cluster" via the empty member set — they
    # show up with jaccard=0 + member_count_now=0 and rank ahead of
    # everything via the jaccard_drift branch.
    rows = conn.execute(
        """
        SELECT cc.concept_id,
               cil.current_cluster_id,
               cc.synthesized_at,
               cc.source_evergreen_slugs_json,
               COALESCE(gc.member_object_ids_json, '[]') AS member_json
          FROM community_crystals cc
          JOIN concept_identity_ledger cil
            ON cil.pack = cc.pack AND cil.concept_id = cc.concept_id
          LEFT JOIN graph_clusters gc
            ON gc.pack = cil.pack
           AND gc.cluster_id = cil.current_cluster_id
           AND gc.cluster_kind = 'louvain_community'
         WHERE cc.pack = ?
           AND cc.superseded_by_synthesized_at = ''
        """,
        (pack,),
    ).fetchall()

    # Pre-fetch open contradictions for the pack — one query, then
    # in-Python membership tests so we don't N+1 the SELECT loop.
    contradiction_members: dict[str, set[str]] = {}
    try:
        contradiction_rows = conn.execute(
            """
            SELECT contradiction_id, subject_key,
                   positive_claim_ids_json, negative_claim_ids_json
              FROM contradictions
             WHERE pack = ?
               AND status = 'open'
            """,
            (pack,),
        ).fetchall()
    except sqlite3.OperationalError:
        contradiction_rows = []

    open_contradiction_object_ids: set[str] = set()
    for _cid, _subj, pos_json, neg_json in contradiction_rows:
        for claims_json in (pos_json, neg_json):
            try:
                claim_ids = json.loads(claims_json)
            except (TypeError, json.JSONDecodeError):
                continue
            for claim_id in claim_ids:
                # claim_id looks like "<object_id>::<claim_suffix>";
                # strip the suffix.  Mirror crystal_scoring's parse.
                head, _, _ = str(claim_id).partition("::")
                if head:
                    open_contradiction_object_ids.add(head)

    out: list[StaleConcept] = []
    for concept_id, current_cluster_id, synth_at, slugs_json, member_json in rows:
        try:
            synth_slugs = set(json.loads(slugs_json) or [])
        except (TypeError, json.JSONDecodeError):
            synth_slugs = set()
        try:
            current_members = set(json.loads(member_json) or [])
        except (TypeError, json.JSONDecodeError):
            current_members = set()

        signals: list[str] = []
        jac: float | None = None

        # (a) jaccard_drift — primary signal.  Empty current_members
        # (no graph_clusters row for current_cluster_id) → jaccard 0,
        # which trips the threshold.
        if synth_slugs or current_members:
            jac = _jaccard(synth_slugs, current_members)
            if jac < JACCARD_STALENESS_THRESHOLD:
                signals.append("jaccard_drift")

        # (b) open_contradiction — any current member is involved in
        # an open contradiction.  We don't require the contradiction
        # to post-date synthesis: the body can't reflect contradictions
        # that exist NOW regardless of when they landed.
        if current_members & open_contradiction_object_ids:
            signals.append("open_contradiction")

        # (c) member_delta — synthesis-time vs current size.
        synth_n = len(synth_slugs)
        now_n = len(current_members)
        delta = abs(now_n - synth_n)
        denom = max(now_n, 1)
        if delta >= MEMBER_DELTA_ABS or delta / denom >= MEMBER_DELTA_RATIO:
            signals.append("member_delta")

        # (d) age — sentinel.
        synth_dt = _parse_iso(synth_at)
        if synth_dt is None or synth_dt < age_cutoff:
            signals.append("age")

        if not signals:
            continue

        # Pick the highest-priority signal as the rank key.
        signals.sort(key=lambda s: _SIGNAL_PRIORITY.get(s, 99))
        out.append(StaleConcept(
            pack=pack,
            concept_id=concept_id,
            current_cluster_id=current_cluster_id or "",
            synthesized_at=synth_at or "",
            signals=tuple(signals),
            primary_signal=signals[0],
            jaccard=jac,
            member_count_now=now_n,
            member_count_at_synth=synth_n,
        ))

    out.sort(key=lambda s: (
        _SIGNAL_PRIORITY.get(s.primary_signal, 99),
        s.jaccard if s.jaccard is not None else 1.0,
        s.synthesized_at,
    ))
    if out:
        logger.info(
            "crystal staleness: %d concepts flagged "
            "(%d jaccard_drift, %d open_contradiction, %d member_delta, %d age)",
            len(out),
            sum(1 for s in out if s.primary_signal == "jaccard_drift"),
            sum(1 for s in out if s.primary_signal == "open_contradiction"),
            sum(1 for s in out if s.primary_signal == "member_delta"),
            sum(1 for s in out if s.primary_signal == "age"),
        )
    return out


__all__ = [
    "AGE_DAYS_SENTINEL",
    "JACCARD_STALENESS_THRESHOLD",
    "MEMBER_DELTA_ABS",
    "MEMBER_DELTA_RATIO",
    "StaleConcept",
    "compute_crystal_staleness",
]
