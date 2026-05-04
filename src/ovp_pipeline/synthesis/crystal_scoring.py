"""Crystal scoring (BL-045, M14).

Derives a per-crystal score from up to five signals over existing
Projections + Canonical-State.  The score drives BL-046's curated
Atlas top-N — without ranking, 329 crystals don't fit any user-
facing scan pattern.

Architecture role: ``crystal_scores`` is a **Projection** in the
six-term contract (see [ARCHITECTURE.md](../../../ARCHITECTURE.md)).
It can be deleted and rebuilt at any time; it never writes
Canonical State.

Five signals, each normalized to [0, 1]:

* **size_norm** — log-scaled community size.  Bigger Louvain
  communities reflect more vault attention.  Weak signal alone.
* **credibility_norm** — sum of source credibility (the table
  named ``source_authority`` despite the misnomer-clash with the
  retired architecture term ``Authority``; it scores per-source
  trustworthiness, e.g. karpathy.com = 0.95).  Sum across the
  crystal's source evergreens, normalized by the per-pack max.
* **contradiction_norm** — count of open contradictions whose
  claims point at evergreens inside the crystal's community.
  Communities with internal tension warrant operator attention.
* **reuse_recency_norm** — rolling 30-day count of ``reuse_events``
  on the crystal's source evergreens.  In M14 v0 this is **always
  zero** because BL-049 (the crystal-specific reuse table) hasn't
  shipped; the column exists for forward compatibility so BL-046
  can already key off it.
* **evergreen_recency_norm** — recency of the most recently
  modified source evergreen, scaled so today = 1.0 and >365 days
  ago = 0.

Default weights:

    0.25 × size_norm        +
    0.30 × credibility_norm +
    0.20 × contradiction_norm +
    0.15 × reuse_recency_norm +
    0.10 × evergreen_recency_norm

Tunable via the ``ScoreWeights`` dataclass; defaults live in
``DEFAULT_WEIGHTS``.  Signals + weights + final score are all
persisted on each row so downstream surfaces can render
"why this crystal is high-scoring" without recomputing.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# Empirical cap for community size.  The OVP vault top community
# is 454; setting MAX a bit above that means the largest community
# saturates near 1.0 without the log-scaling losing resolution at
# the small end.  After M14 BL-048 the splitter caps communities
# at 50 members, so 500 is now well above the natural ceiling and
# only matters as a defensive saturation bound.
_SIZE_LOG_CAP = 500.0

# Recency window in days; older evergreens get score 0.
_RECENCY_WINDOW_DAYS = 365.0

# M14 BL-049: rolling window for crystal-scoped ``reuse_events``.
# Shorter than the evergreen-recency window because reuse signals
# are intrinsically more volatile — a crystal that was opened 30
# days ago doesn't carry the same "still hot" signal an evergreen
# touched a year ago does.
_REUSE_RECENCY_WINDOW_DAYS = 30.0

# ``object_kind`` values that the reuse-event recency signal
# attributes to crystals.  Surfaces emitting reuse events for a
# crystal must use one of these labels for the signal to register.
_CRYSTAL_REUSE_KINDS = ("community_crystal", "contradiction_crystal")


@dataclass(frozen=True, slots=True)
class ScoreSignals:
    size_norm: float = 0.0
    credibility_norm: float = 0.0
    contradiction_norm: float = 0.0
    reuse_recency_norm: float = 0.0
    evergreen_recency_norm: float = 0.0


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    size: float = 0.25
    credibility: float = 0.30
    contradiction: float = 0.20
    reuse_recency: float = 0.15
    evergreen_recency: float = 0.10

    def total(self) -> float:
        return (
            self.size + self.credibility + self.contradiction
            + self.reuse_recency + self.evergreen_recency
        )


DEFAULT_WEIGHTS = ScoreWeights()


@dataclass(frozen=True, slots=True)
class CrystalScore:
    pack: str
    crystal_kind: str          # 'community' | 'contradiction'
    crystal_id: str
    score: float
    signals: ScoreSignals
    computed_at: str


# ----- Pure scoring math --------------------------------------------------


def compute_score(signals: ScoreSignals, weights: ScoreWeights = DEFAULT_WEIGHTS) -> float:
    """Weighted sum.  Outputs a float in [0, 1] when all signals are
    in [0, 1] and the weights sum to 1.0 (the default)."""
    return (
        weights.size * signals.size_norm
        + weights.credibility * signals.credibility_norm
        + weights.contradiction * signals.contradiction_norm
        + weights.reuse_recency * signals.reuse_recency_norm
        + weights.evergreen_recency * signals.evergreen_recency_norm
    )


def _size_signal(member_count: int, *, cap: float = _SIZE_LOG_CAP) -> float:
    """Log-scale a community size to [0, 1] with ``cap`` as the
    saturation point.  Saturates rather than clipping so that a
    600-member community doesn't outrank a 454-member one
    massively in a vault where 454 is already the empirical max.
    """
    if member_count <= 0:
        return 0.0
    return min(1.0, math.log(member_count + 1) / math.log(cap + 1))


def _credibility_signal(
    raw_sum: float, max_observed: float,
) -> float:
    """Normalize a per-crystal credibility sum against the per-pack
    max.  Highest-credibility crystal in the pack scores 1.0;
    others scale proportionally."""
    if max_observed <= 0:
        return 0.0
    return max(0.0, min(1.0, raw_sum / max_observed))


def _contradiction_signal(
    raw_count: int, max_observed: int,
) -> float:
    """Normalize a per-crystal contradiction count against the per-
    pack max.  Crystals with zero open contradictions score 0;
    the most-contradicted crystal scores 1."""
    if max_observed <= 0:
        return 0.0
    return max(0.0, min(1.0, raw_count / max_observed))


def _reuse_recency_signal(
    raw_count: int, max_observed: int,
) -> float:
    """Normalize a per-crystal reuse-event count against the per-pack
    max within the rolling window.  Crystals with zero events score
    0; the most-touched crystal scores 1.  Cold-start (no events at
    all) → all crystals = 0, identical to the BL-045 v0 placeholder.
    """
    if max_observed <= 0:
        return 0.0
    return max(0.0, min(1.0, raw_count / max_observed))


def _evergreen_recency_signal(
    most_recent_mtime_utc: float | None,
    *,
    now_utc: float | None = None,
    window_days: float = _RECENCY_WINDOW_DAYS,
) -> float:
    """Score the freshness of the most recently modified source
    evergreen.  Today = 1.0, ``window_days`` ago = 0.0.  Linear
    decay so a crystal that absorbed a new evergreen recently
    outranks one whose sources have all gone quiet."""
    if most_recent_mtime_utc is None or window_days <= 0:
        return 0.0
    now = now_utc if now_utc is not None else datetime.now(timezone.utc).timestamp()
    age_days = max(0.0, (now - most_recent_mtime_utc) / 86_400.0)
    if age_days >= window_days:
        return 0.0
    return 1.0 - (age_days / window_days)


# ----- DB helpers --------------------------------------------------------


def _load_community_index(
    conn: sqlite3.Connection, pack: str,
) -> dict[str, dict]:
    """Return ``{cluster_id: {label, members, source_slugs}}`` for every
    Louvain community that has at least one current crystal row in
    ``pack``."""
    out: dict[str, dict] = {}
    rows = conn.execute(
        """
        SELECT gc.cluster_id, gc.label, gc.member_object_ids_json,
               cc.source_evergreen_slugs_json
          FROM graph_clusters gc
          JOIN community_crystals cc
            ON cc.pack = gc.pack AND cc.cluster_id = gc.cluster_id
         WHERE gc.pack = ?
           AND gc.cluster_kind = 'louvain_community'
           AND cc.superseded_by_synthesized_at = ''
        """,
        (pack,),
    ).fetchall()
    for cluster_id, label, members_json, slugs_json in rows:
        try:
            members = list(json.loads(members_json))
            slugs = list(json.loads(slugs_json))
        except (TypeError, json.JSONDecodeError):
            logger.warning("malformed JSON for cluster %s; skipping",
                           cluster_id)
            continue
        out[cluster_id] = {
            "label": label,
            "members": members,
            "source_slugs": slugs,
        }
    return out


def _load_contradiction_index(
    conn: sqlite3.Connection, pack: str,
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    rows = conn.execute(
        """
        SELECT contradiction_id, subject_key, source_object_ids_json
          FROM contradiction_crystals
         WHERE pack = ?
           AND superseded_by_synthesized_at = ''
        """,
        (pack,),
    ).fetchall()
    for cid, subject, srcs_json in rows:
        try:
            sources = list(json.loads(srcs_json))
        except (TypeError, json.JSONDecodeError):
            logger.warning("malformed JSON for contradiction %s; skipping",
                           cid)
            continue
        out[cid] = {"subject_key": subject, "source_object_ids": sources}
    return out


def _load_source_credibility(
    conn: sqlite3.Connection,
) -> dict[str, float]:
    """Map source_id → credibility score from ``source_authority``
    table.  Returns empty dict if the table doesn't exist (a fresh
    vault that hasn't run ``ovp-score-sources`` yet)."""
    try:
        rows = conn.execute(
            "SELECT source_id, authority FROM source_authority"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {sid: float(auth) for sid, auth in rows}


def _load_object_metadata(
    conn: sqlite3.Connection, pack: str, object_ids: set[str],
) -> dict[str, tuple[str, str]]:
    """Single-query lookup for object_id → (source_slug, canonical_path).

    Pre-fix the scoring rebuild ran two near-identical queries — one
    for slug, one for path — over the same id set.  One round-trip
    is enough; both columns come from the same row.  Chunked at
    500 ids to stay below SQLite's parameter cap.
    """
    if not object_ids:
        return {}
    out: dict[str, tuple[str, str]] = {}
    chunk = 500
    ids = sorted(object_ids)
    for start in range(0, len(ids), chunk):
        batch = ids[start:start + chunk]
        placeholders = ",".join("?" * len(batch))
        cur = conn.execute(
            f"SELECT object_id, source_slug, canonical_path FROM objects "
            f"WHERE pack = ? AND object_id IN ({placeholders})",
            (pack, *batch),
        )
        for object_id, slug, path in cur:
            out[object_id] = (slug or "", path or "")
    return out


def _load_crystal_reuse_counts(
    conn: sqlite3.Connection,
    pack: str,
    *,
    now_iso: str,
    window_days: float = _REUSE_RECENCY_WINDOW_DAYS,
) -> dict[tuple[str, str], int]:
    """Count crystal-scoped ``reuse_events`` in the rolling window.
    Returns ``{(crystal_kind, crystal_id): count}`` where
    ``crystal_kind`` matches the ``crystal_scores.crystal_kind`` form
    (``'community'`` or ``'contradiction'``, NOT the
    ``object_kind`` used in the events table which is
    ``'community_crystal'`` / ``'contradiction_crystal'``).

    No-op when the ``reuse_events`` table doesn't exist or carries
    no crystal-tagged rows in window — returns an empty dict, which
    means the recency signal stays at the cold-start zero across
    the corpus until surfaces start emitting events.
    """
    # ts is stored as ISO-8601 text.  Lexicographic comparison on
    # ISO strings is order-preserving for same-zone timestamps,
    # which is what the rest of the codebase assumes.
    cutoff_dt = datetime.fromisoformat(now_iso) - timedelta(days=window_days)
    cutoff_iso = cutoff_dt.isoformat(timespec="seconds")
    placeholders = ",".join("?" * len(_CRYSTAL_REUSE_KINDS))
    try:
        rows = conn.execute(
            f"SELECT object_kind, object_id, COUNT(*) "
            f"FROM reuse_events "
            f"WHERE pack = ? AND object_kind IN ({placeholders}) "
            f"  AND ts >= ? "
            f"GROUP BY object_kind, object_id",
            (pack, *_CRYSTAL_REUSE_KINDS, cutoff_iso),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[tuple[str, str], int] = {}
    for object_kind, object_id, n in rows:
        # Map the event-side ``object_kind`` to the score-side
        # ``crystal_kind`` shorthand.
        if object_kind == "community_crystal":
            kind = "community"
        elif object_kind == "contradiction_crystal":
            kind = "contradiction"
        else:
            continue
        out[(kind, object_id)] = int(n)
    return out


def _load_open_contradictions(
    conn: sqlite3.Connection, pack: str,
) -> list[tuple[set[str], set[str]]]:
    """Return list of (positive_object_ids, negative_object_ids) for
    every open contradiction.  Used to score communities by how
    many open contradictions touch their members."""
    rows = conn.execute(
        "SELECT positive_claim_ids_json, negative_claim_ids_json "
        "FROM contradictions WHERE pack = ? AND status = 'open'",
        (pack,),
    ).fetchall()
    out: list[tuple[set[str], set[str]]] = []
    for pos_json, neg_json in rows:
        try:
            pos = {c.split("::", 1)[0] for c in json.loads(pos_json)}
            neg = {c.split("::", 1)[0] for c in json.loads(neg_json)}
        except (TypeError, json.JSONDecodeError):
            continue
        out.append((pos, neg))
    return out


# ----- Vault filesystem mtime --------------------------------------------


def _evergreen_mtimes(
    vault_dir: Path, paths_by_object: dict[str, str],
) -> dict[str, float]:
    """Map object_id → most recent mtime (UTC seconds) of the
    canonical_path file.  Skips missing files (those just won't
    contribute to the recency signal)."""
    vault_root = vault_dir.resolve()
    out: dict[str, float] = {}
    for object_id, rel in paths_by_object.items():
        if not rel:
            continue
        full = vault_dir / rel
        try:
            full.resolve().relative_to(vault_root)
        except ValueError:
            continue
        try:
            out[object_id] = full.stat().st_mtime
        except (OSError, FileNotFoundError):
            continue
    return out


# ----- Main rebuild -------------------------------------------------------


def rebuild_crystal_scores(
    conn: sqlite3.Connection,
    *,
    vault_dir: Path,
    pack: str,
    weights: ScoreWeights = DEFAULT_WEIGHTS,
) -> list[CrystalScore]:
    """Recompute scores for every current crystal in ``pack`` and
    overwrite the ``crystal_scores`` rows.  Returns the list of
    scores produced.

    Idempotent: re-running with no input changes produces identical
    scores.  Operates entirely on derived state — no Canonical State
    is read or written.
    """
    community_index = _load_community_index(conn, pack)
    contradiction_index = _load_contradiction_index(conn, pack)
    if not community_index and not contradiction_index:
        # Nothing to score; clear stale rows for this pack and return.
        conn.execute("DELETE FROM crystal_scores WHERE pack = ?", (pack,))
        conn.commit()
        return []

    source_credibility = _load_source_credibility(conn)
    open_contradictions = _load_open_contradictions(conn, pack)

    # Gather all object_ids referenced by any crystal so we can
    # batch-load the metadata once.
    all_object_ids: set[str] = set()
    for entry in community_index.values():
        all_object_ids.update(entry["source_slugs"])
        all_object_ids.update(entry["members"])
    for entry in contradiction_index.values():
        all_object_ids.update(entry["source_object_ids"])
    object_metadata = _load_object_metadata(conn, pack, all_object_ids)
    object_source_slugs = {
        oid: meta[0] for oid, meta in object_metadata.items()
    }
    object_paths = {oid: meta[1] for oid, meta in object_metadata.items()}
    object_mtimes = _evergreen_mtimes(vault_dir, object_paths)

    # Pre-compute per-pack maxima for normalization.
    raw_credibility_sums: dict[tuple[str, str], float] = {}
    raw_contradiction_counts: dict[tuple[str, str], int] = {}

    def _credibility_sum(slugs_or_ids: list[str]) -> float:
        total = 0.0
        for oid in slugs_or_ids:
            slug = object_source_slugs.get(oid, "")
            if not slug:
                continue
            total += source_credibility.get(slug, 0.0)
        return total

    # Inverted index: object_id → set of contradiction indices it
    # appears in.  Pre-fix ``_contradiction_count`` was
    # O(N_crystals × N_contradictions) per scoring rebuild; this
    # makes it O(N_members) per crystal which scales with vault
    # size, not contradiction count.
    object_to_contradictions: dict[str, set[int]] = {}
    for idx, (pos, neg) in enumerate(open_contradictions):
        for oid in pos | neg:
            object_to_contradictions.setdefault(oid, set()).add(idx)

    def _contradiction_count(member_set: set[str]) -> int:
        # Each contradiction is counted at most once even when it
        # touches multiple members of the same crystal.
        found: set[int] = set()
        for oid in member_set:
            indices = object_to_contradictions.get(oid)
            if indices:
                found.update(indices)
        return len(found)

    # First pass: compute raw values.
    for cid, entry in community_index.items():
        raw_credibility_sums[("community", cid)] = _credibility_sum(
            entry["source_slugs"],
        )
        raw_contradiction_counts[("community", cid)] = _contradiction_count(
            set(entry["members"]),
        )
    for cid, entry in contradiction_index.items():
        raw_credibility_sums[("contradiction", cid)] = _credibility_sum(
            entry["source_object_ids"],
        )
        # A contradiction crystal IS the contradiction by definition;
        # counting "how many open contradictions touch its sources"
        # is at minimum 1 (itself).
        raw_contradiction_counts[("contradiction", cid)] = _contradiction_count(
            set(entry["source_object_ids"]),
        )

    max_credibility = max(raw_credibility_sums.values(), default=0.0)
    max_contradictions = max(raw_contradiction_counts.values(), default=0)

    # Second pass: build CrystalScore rows.
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    now_ts = datetime.now(timezone.utc).timestamp()
    out: list[CrystalScore] = []

    # M14 BL-049: pull crystal-scoped reuse counts within the rolling
    # 30-day window.  Cold start (no events at all) leaves every
    # entry's count at zero, which produces the same all-zero
    # behaviour as the BL-045 v0 placeholder.
    reuse_counts = _load_crystal_reuse_counts(conn, pack, now_iso=now_iso)
    max_reuse = max(reuse_counts.values(), default=0)

    for cid, entry in community_index.items():
        size = len(entry["members"])
        slug_set = set(entry["source_slugs"])
        most_recent = max(
            (object_mtimes[oid] for oid in slug_set if oid in object_mtimes),
            default=None,
        )
        signals = ScoreSignals(
            size_norm=_size_signal(size),
            credibility_norm=_credibility_signal(
                raw_credibility_sums[("community", cid)], max_credibility,
            ),
            contradiction_norm=_contradiction_signal(
                raw_contradiction_counts[("community", cid)], max_contradictions,
            ),
            reuse_recency_norm=_reuse_recency_signal(
                reuse_counts.get(("community", cid), 0), max_reuse,
            ),
            evergreen_recency_norm=_evergreen_recency_signal(
                most_recent, now_utc=now_ts,
            ),
        )
        out.append(CrystalScore(
            pack=pack, crystal_kind="community", crystal_id=cid,
            score=compute_score(signals, weights),
            signals=signals, computed_at=now_iso,
        ))

    for cid, entry in contradiction_index.items():
        sources = entry["source_object_ids"]
        size = len(sources)
        most_recent = max(
            (object_mtimes[oid] for oid in sources if oid in object_mtimes),
            default=None,
        )
        signals = ScoreSignals(
            # Contradictions are usually small (2-5 sources), so the
            # log-cap of 500 keeps them naturally low on size_norm —
            # which is correct: their value is in surfacing tension,
            # not in covering territory.
            size_norm=_size_signal(size),
            credibility_norm=_credibility_signal(
                raw_credibility_sums[("contradiction", cid)], max_credibility,
            ),
            contradiction_norm=_contradiction_signal(
                raw_contradiction_counts[("contradiction", cid)],
                max_contradictions,
            ),
            reuse_recency_norm=_reuse_recency_signal(
                reuse_counts.get(("contradiction", cid), 0), max_reuse,
            ),
            evergreen_recency_norm=_evergreen_recency_signal(
                most_recent, now_utc=now_ts,
            ),
        )
        out.append(CrystalScore(
            pack=pack, crystal_kind="contradiction", crystal_id=cid,
            score=compute_score(signals, weights),
            signals=signals, computed_at=now_iso,
        ))

    # Single transaction for atomicity.
    try:
        conn.execute("DELETE FROM crystal_scores WHERE pack = ?", (pack,))
        conn.executemany(
            """
            INSERT INTO crystal_scores
                (pack, crystal_kind, crystal_id, score,
                 size_norm, credibility_norm, contradiction_norm,
                 reuse_recency_norm, evergreen_recency_norm,
                 computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.pack, s.crystal_kind, s.crystal_id, s.score,
                    s.signals.size_norm, s.signals.credibility_norm,
                    s.signals.contradiction_norm,
                    s.signals.reuse_recency_norm,
                    s.signals.evergreen_recency_norm,
                    s.computed_at,
                )
                for s in out
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return out
