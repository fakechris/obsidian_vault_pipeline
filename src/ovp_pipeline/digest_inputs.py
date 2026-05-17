"""Digest data collector + readiness preflight (M23 / BL-094).

Replaces the crystal-only input collector from M20's
:mod:`commands.digest_handler` with a four-layer schema:

* **Layer 0** — today's intake from ``audit_events`` (acknowledgment)
* **Layer 1** — evergreen delta from ``evergreen_revisions`` (the spine)
* **Layer 2** — connection to existing crystals + contradictions
* **Layer 3** — pipeline state with stale-crystal flag (backpressure)

What this module owns
---------------------

* **Window resolution** — operator-local
  ``[last_successful_digest_at, as_of]`` with local-day fallback.
* **Preflight checks** — verifies ``knowledge.db`` has the rows each
  layer needs *before* the handler builds a prompt.  Handler renders
  degraded sections rather than crashing.
* **Layer collectors** — deterministic SQL + audit-log reads, no LLM.
* **Input hash** — stable identifier set hashed for Stage 3's
  idempotency gate.

What this module does NOT own
-----------------------------

* LLM prompt or call — :mod:`commands.digest_handler` consumes
  :class:`DigestInputs` to build its prompt; Stage 3 swaps the
  prompt body.
* Audit-event emission for digest results — that's Stage 5's
  ``digest_clicked_through`` / ``digest_question_acted_on``.
* The handler's ``_today_utc_date()`` filename — Stage 3 swaps
  that to operator-local.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Iterable

from ovp_pipeline.audit_identity import audit_slug_for_column
from ovp_pipeline.audit_time import parse_audit_ts
from ovp_pipeline.digest_config import (
    DigestConfig,
    load_digest_config,
    resolve_timezone,
)

logger = logging.getLogger(__name__)


_KNOWLEDGE_DB_REL: Final[str] = "60-Logs/knowledge.db"
_CHANGE_NOTE_QUALITY_SAMPLE: Final[int] = 10
_CHANGE_NOTE_QUALITY_MIN_LEN: Final[int] = 20
_CHANGE_NOTE_QUALITY_PASS_RATIO: Final[float] = 0.5
# How far back to look when checking "evergreen_revisions has recent rows".
# Wider than the window so the preflight tolerates quiet days without
# falsely flagging an empty Layer 1 as a missing-data error.
_PREFLIGHT_RECENT_DAYS: Final[int] = 7

# Module-level stopword set for Layer 0 keyword chips.  Recomputing
# this on every digest run is wasted work — operator runs the digest
# at least daily, the set never changes.  (gemini-code-assist nitpick)
_LAYER0_STOPWORDS: Final[frozenset[str]] = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "your",
    "into", "what", "how", "why", "are", "was", "were", "will",
    "you", "can", "all", "any", "but", "not", "use", "via",
})


# ---------------------------------------------------------------
# Public schema
# ---------------------------------------------------------------


@dataclass(frozen=True)
class IntakeLayer:
    """Layer 0 — operator's intake in the window."""

    intake_events_processed: int
    topic_distribution: tuple[tuple[str, int], ...]
    authors_or_sources: tuple[str, ...]
    representative_samples: tuple[str, ...]
    # BL-106: distinct sources whose FIRST durable intake falls in
    # the window (intake-time axis).  A day's digest must acknowledge
    # articles saved that day even when absorb/synthesis runs later;
    # ``intake_events_processed`` (event-time, raw rows) misses that.
    intake_cohort_sources: int = 0


@dataclass(frozen=True)
class EvergreenDelta:
    """One row from ``evergreen_revisions`` enriched with cluster
    membership."""

    object_id: str
    title: str
    version: int
    change_type: str
    derived_at: str  # ISO timestamp as stored
    change_summary: str  # change_note when meaningful, else fallback
    cluster_id: str  # empty when unclassified


@dataclass(frozen=True)
class DeltaLayer:
    """Layer 1 — what materially changed in the window."""

    new_evergreens: tuple[EvergreenDelta, ...]
    updated_evergreens: tuple[EvergreenDelta, ...]


@dataclass(frozen=True)
class ConnectionLayer:
    """Layer 2 — how the window connects to prior thinking."""

    connected_community_crystals: tuple[tuple[str, str], ...]  # (cluster_id, label)
    touched_contradictions: tuple[tuple[str, str], ...]  # (contradiction_id, subject)
    recent_top_crystals: tuple[tuple[str, str, float, str], ...]
    # (id, kind, score, label) — label is the human-readable
    # name from ``graph_clusters.label`` (community) or
    # ``contradiction_crystals.subject_key`` (contradiction).
    # Empty string when no label is known.


@dataclass(frozen=True)
class PipelineState:
    """Layer 3 — backpressure visibility, with stale-crystal flag."""

    unsynthesized_evergreens: int
    last_synthesis_at: str  # ISO or empty
    clusters_at_threshold: tuple[tuple[str, str, int, bool], ...]
    # (cluster_id, label, evergreen_count, stale_crystal_flag)
    open_contradictions_count: int


@dataclass(frozen=True)
class PreflightReport:
    """Outcome of the data-readiness preflight.

    Every check returns one of ``ok`` / ``degraded`` / ``unavailable``.
    Handler reads this report to decide which sections render normally
    vs. degraded vs. as honest "data not available" stubs.
    """

    evergreen_revisions_table: str
    evergreen_revisions_recent: str
    audit_events_layer0: str
    change_note_quality: str
    graph_clusters: str
    community_crystals: str

    def any_degraded(self) -> bool:
        return any(
            v != "ok"
            for v in (
                self.evergreen_revisions_table,
                self.evergreen_revisions_recent,
                self.audit_events_layer0,
                self.change_note_quality,
                self.graph_clusters,
                self.community_crystals,
            )
        )


@dataclass(frozen=True)
class DigestInputs:
    """Result of :func:`collect_digest_inputs`.

    Frozen value object — every layer is a tuple-of-frozen-dataclass
    so the entire snapshot is safe to pass across module boundaries
    without defensive copies.
    """

    pack: str
    window_start: datetime
    window_end: datetime
    tz_name: str
    config: DigestConfig
    preflight: PreflightReport
    intake: IntakeLayer
    delta: DeltaLayer
    connections: ConnectionLayer
    pipeline_state: PipelineState

    def input_hash(self) -> str:
        """Stage 3 idempotency-gate hash.

        Includes only **stable identifiers** + window boundaries —
        never wall-clock prose, never aggregate counts that drift
        across runs.  Two runs within the same window with the same
        underlying rows produce the same hash; cross-window runs
        always differ.
        """
        return _compute_input_hash(self)


# ---------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------


def collect_digest_inputs(
    vault_dir: Path | str,
    pack: str,
    *,
    as_of: datetime | None = None,
    config: DigestConfig | None = None,
) -> DigestInputs:
    """Build the full :class:`DigestInputs` for one digest run.

    Parameters
    ----------
    vault_dir
        Vault root.  ``knowledge.db`` resolves to
        ``<vault>/60-Logs/knowledge.db``; ``audit_events`` is read
        from there too.
    pack
        Pack name to filter crystals + contradictions by.  Layer 0/1
        are pack-agnostic (audit events + revisions span the whole
        vault).
    as_of
        Window end.  Defaults to ``datetime.now(tz)``.  Pass a
        deterministic value in tests.
    config
        Pre-loaded :class:`DigestConfig`.  Resolves from
        ``<vault>/.ovp/digest.yaml`` when omitted.

    Returns a :class:`DigestInputs` whose preflight field encodes any
    degraded layers; the handler decides how to render them.  Never
    raises on data shape — only on caller mistakes (missing vault).
    """
    if config is None:
        config = load_digest_config(vault_dir)
    tz = resolve_timezone(config)
    tz_name = _tz_display_name(tz)
    if as_of is None:
        as_of = datetime.now(tz)
    elif as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=tz)
    else:
        as_of = as_of.astimezone(tz)

    db_path = Path(vault_dir) / _KNOWLEDGE_DB_REL

    # Window resolution requires the DB — read last successful
    # digest before any other query so a missing DB short-circuits
    # cleanly to the local-day fallback.
    window_start = _resolve_window_start(db_path, as_of)
    window_end = as_of

    if not db_path.is_file():
        return _empty_inputs(
            pack=pack,
            window_start=window_start,
            window_end=window_end,
            tz_name=tz_name,
            config=config,
            preflight=PreflightReport(
                evergreen_revisions_table="unavailable",
                evergreen_revisions_recent="unavailable",
                audit_events_layer0="unavailable",
                change_note_quality="unavailable",
                graph_clusters="unavailable",
                community_crystals="unavailable",
            ),
        )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        preflight = _run_preflight(conn, window_start, window_end, config)
        intake = _collect_layer0(conn, window_start, window_end, config)
        delta = _collect_layer1(conn, pack, window_start, window_end, preflight)
        connections = _collect_layer2(conn, pack, delta)
        pipeline_state = _collect_layer3(conn, pack, config)

    return DigestInputs(
        pack=pack,
        window_start=window_start,
        window_end=window_end,
        tz_name=tz_name,
        config=config,
        preflight=preflight,
        intake=intake,
        delta=delta,
        connections=connections,
        pipeline_state=pipeline_state,
    )


# ---------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------


def _resolve_window_start(db_path: Path, as_of: datetime) -> datetime:
    """Return the window start: last successful digest, else local-day
    boundary containing ``as_of``.

    Falls through to the local-day boundary on any read failure so
    the first-ever digest run gets a sensible window.
    """
    last_successful = _read_last_successful_digest(db_path)
    local_day = _local_day_boundary(as_of)
    if last_successful is not None and last_successful >= local_day:
        # Mid-day regeneration — start from the prior run.
        return last_successful.astimezone(as_of.tzinfo)
    return local_day


def _read_last_successful_digest(db_path: Path) -> datetime | None:
    """Read the most recent ``digest_generated`` audit event's
    timestamp.  ``None`` when none exist or the table is missing."""
    if not db_path.is_file():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT timestamp FROM audit_events
                 WHERE event_type = 'digest_generated'
                 ORDER BY timestamp DESC
                 LIMIT 1
                """
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or not row[0]:
        return None
    return _parse_iso(row[0])


def _local_day_boundary(as_of: datetime) -> datetime:
    """Midnight at the start of ``as_of``'s local day.

    ``as_of`` must be tz-aware.  Returns a tz-aware datetime in the
    same timezone.
    """
    if as_of.tzinfo is None:  # defensive — caller should have set it
        raise ValueError("_local_day_boundary requires a tz-aware datetime")
    return as_of.replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------


def _run_preflight(
    conn: sqlite3.Connection,
    window_start: datetime,
    window_end: datetime,
    config: DigestConfig,
) -> PreflightReport:
    """Six checks, each returning ``ok`` / ``degraded`` / ``unavailable``.

    The handler treats ``degraded`` as "render with caveat" and
    ``unavailable`` as "render an honest skipped section".  None of
    the checks raise — every failure path returns a status string.
    """
    table_status = _check_table_exists(conn, "evergreen_revisions")
    if table_status != "ok":
        evergreen_recent = "unavailable"
        change_note = "unavailable"
    else:
        evergreen_recent = _check_evergreen_revisions_recent(conn)
        change_note = _check_change_note_quality(conn)

    audit_status = _check_audit_layer0(conn, window_start, window_end, config)
    clusters_status = _check_table_nonempty(conn, "graph_clusters")
    crystals_status = _check_table_nonempty(conn, "community_crystals")

    return PreflightReport(
        evergreen_revisions_table=table_status,
        evergreen_revisions_recent=evergreen_recent,
        audit_events_layer0=audit_status,
        change_note_quality=change_note,
        graph_clusters=clusters_status,
        community_crystals=crystals_status,
    )


def _check_table_exists(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return "ok" if row else "unavailable"


def _check_table_nonempty(conn: sqlite3.Connection, name: str) -> str:
    if _check_table_exists(conn, name) != "ok":
        return "unavailable"
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    except sqlite3.OperationalError:
        return "unavailable"
    return "ok" if count > 0 else "degraded"


def _check_evergreen_revisions_recent(conn: sqlite3.Connection) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_PREFLIGHT_RECENT_DAYS)).isoformat()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM evergreen_revisions WHERE derived_at >= ?",
            (cutoff,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return "unavailable"
    return "ok" if count > 0 else "degraded"


def _check_change_note_quality(conn: sqlite3.Connection) -> str:
    """Sample the N most recent non-empty ``change_note`` rows; pass
    when ≥ 50% have meaningful text (≥ 20 chars and not a generic
    ``lifecycle=…`` marker)."""
    try:
        rows = conn.execute(
            """
            SELECT change_note FROM evergreen_revisions
             WHERE change_note IS NOT NULL AND change_note != ''
             ORDER BY derived_at DESC
             LIMIT ?
            """,
            (_CHANGE_NOTE_QUALITY_SAMPLE,),
        ).fetchall()
    except sqlite3.OperationalError:
        return "unavailable"
    if not rows:
        return "degraded"
    meaningful = sum(1 for r in rows if _is_meaningful_change_note(r[0]))
    return "ok" if meaningful / len(rows) >= _CHANGE_NOTE_QUALITY_PASS_RATIO else "degraded"


def _is_meaningful_change_note(note: str) -> bool:
    text = (note or "").strip()
    if len(text) < _CHANGE_NOTE_QUALITY_MIN_LEN:
        return False
    # Skip generic markers the absorber emits when it has nothing
    # human-readable to say.
    generic_prefixes = ("lifecycle=", "auto:", "fixup:")
    if any(text.startswith(p) for p in generic_prefixes):
        return False
    return True


def _check_audit_layer0(
    conn: sqlite3.Connection,
    window_start: datetime,
    window_end: datetime,
    config: DigestConfig,
) -> str:
    if not config.intake_event_types:
        return "degraded"
    placeholders = ",".join("?" * len(config.intake_event_types))
    try:
        count = conn.execute(
            f"""
            SELECT COUNT(*) FROM audit_events
             WHERE event_type IN ({placeholders})
               AND timestamp >= ?
               AND timestamp <= ?
            """,
            (
                *config.intake_event_types,
                _utc_iso(window_start),
                _utc_iso(window_end),
            ),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return "unavailable"
    return "ok" if count > 0 else "degraded"


# ---------------------------------------------------------------
# Layer 0 — intake
# ---------------------------------------------------------------


def _collect_layer0(
    conn: sqlite3.Connection,
    window_start: datetime,
    window_end: datetime,
    config: DigestConfig,
) -> IntakeLayer:
    if not config.intake_event_types:
        return IntakeLayer(0, (), (), ())
    placeholders = ",".join("?" * len(config.intake_event_types))
    try:
        rows = conn.execute(
            f"""
            SELECT slug, payload_json FROM audit_events
             WHERE event_type IN ({placeholders})
               AND timestamp >= ?
               AND timestamp <= ?
             ORDER BY timestamp DESC
            """,
            (
                *config.intake_event_types,
                _utc_iso(window_start),
                _utc_iso(window_end),
            ),
        ).fetchall()
    except sqlite3.OperationalError:
        return IntakeLayer(0, (), (), ())

    titles: list[str] = []
    authors: set[str] = set()
    for row in rows:
        slug = (row["slug"] or "").strip()
        payload = _safe_json(row["payload_json"])
        title = (
            (payload.get("title") if isinstance(payload, dict) else None)
            or slug
            or ""
        )
        if title:
            titles.append(title)
        if isinstance(payload, dict):
            author = payload.get("author") or payload.get("source_domain") or ""
            if isinstance(author, str) and author.strip():
                authors.add(author.strip())

    topic_dist = _top_keyword_distribution(titles)
    samples = tuple(titles[:5])
    cohort = _intake_cohort_count(
        conn, window_start, window_end, config
    )
    return IntakeLayer(
        intake_events_processed=len(rows),
        topic_distribution=topic_dist,
        authors_or_sources=tuple(sorted(authors)),
        representative_samples=samples,
        intake_cohort_sources=cohort,
    )


def _intake_cohort_count(
    conn: sqlite3.Connection,
    window_start: datetime,
    window_end: datetime,
    config: DigestConfig,
) -> int:
    """BL-106: distinct sources whose EARLIEST intake event (all
    history) lands in [window_start, window_end].

    The lifecycle kernel derives state from cumulative evidence, so
    a source's first intake event is the moment it entered the
    pipeline — the intake-time axis BL-105 uses.  Scans the intake
    subset (BL-108 streaming is the perf follow-up); identity via
    the shared ``audit_slug_for_column`` so it matches the cards.
    """
    if not config.intake_event_types:
        return 0
    placeholders = ",".join("?" * len(config.intake_event_types))
    try:
        rows = conn.execute(
            f"SELECT slug, payload_json, timestamp FROM audit_events "
            f" WHERE event_type IN ({placeholders})",
            tuple(config.intake_event_types),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    earliest: dict[str, datetime] = {}
    for row in rows:
        parsed = parse_audit_ts(str(row["timestamp"] or ""))
        if parsed is None:
            continue
        slug = (row["slug"] or "").strip()
        if not slug:
            payload = _safe_json(row["payload_json"])
            if isinstance(payload, dict):
                slug = audit_slug_for_column(payload)
        if not slug:
            continue
        cur = earliest.get(slug)
        if cur is None or parsed < cur:
            earliest[slug] = parsed
    return sum(
        1
        for ts in earliest.values()
        if window_start <= ts <= window_end
    )


def _top_keyword_distribution(titles: Iterable[str]) -> tuple[tuple[str, int], ...]:
    """Lightweight keyword counts.

    Splits each title on whitespace, lowercases, drops stopwords and
    short tokens, and returns the top 5 by frequency.  Good enough
    for "memory (7), AI agents (3), operations (2)" style chips —
    a real topic model can replace this later.
    """
    counts: dict[str, int] = {}
    for title in titles:
        for raw in title.lower().split():
            token = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
            if len(token) < 4 or token in _LAYER0_STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
    ordered = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return tuple(ordered[:5])


# ---------------------------------------------------------------
# Layer 1 — evergreen delta
# ---------------------------------------------------------------


def _collect_layer1(
    conn: sqlite3.Connection,
    pack: str,
    window_start: datetime,
    window_end: datetime,
    preflight: PreflightReport,
) -> DeltaLayer:
    if preflight.evergreen_revisions_table != "ok":
        return DeltaLayer((), ())
    try:
        # Codex P2: ``objects`` is keyed by ``(pack, object_id)`` in
        # multi-pack vaults; joining on object_id alone can pick a
        # foreign-pack title.  Pack-scope both the revision filter
        # and the objects join.
        rows = conn.execute(
            """
            SELECT er.object_id, er.version, er.change_type,
                   er.derived_at, er.change_note,
                   o.title
              FROM evergreen_revisions er
              LEFT JOIN objects o
                ON o.pack = er.pack AND o.object_id = er.object_id
             WHERE er.pack = ?
               AND er.derived_at >= ?
               AND er.derived_at <= ?
             ORDER BY er.derived_at DESC
            """,
            (pack, _utc_iso(window_start), _utc_iso(window_end)),
        ).fetchall()
    except sqlite3.OperationalError:
        return DeltaLayer((), ())

    cluster_index = _build_cluster_membership_index(conn, pack=pack)
    new_rows: list[EvergreenDelta] = []
    updated_rows: list[EvergreenDelta] = []

    for row in rows:
        oid = row["object_id"] or ""
        version = int(row["version"] or 0)
        change_type = (row["change_type"] or "").strip()
        change_note = row["change_note"] or ""
        derived_at = row["derived_at"] or ""
        title = row["title"] or oid
        cluster_id = cluster_index.get(oid, "")

        change_summary = _format_change_summary(
            change_note=change_note,
            change_type=change_type,
            version=version,
            preflight=preflight,
        )

        delta = EvergreenDelta(
            object_id=oid,
            title=title,
            version=version,
            change_type=change_type,
            derived_at=derived_at,
            change_summary=change_summary,
            cluster_id=cluster_id,
        )

        if version == 1 and change_type in {"created", "promote", "extract"}:
            new_rows.append(delta)
        else:
            updated_rows.append(delta)

    return DeltaLayer(
        new_evergreens=tuple(new_rows),
        updated_evergreens=tuple(updated_rows),
    )


def _format_change_summary(
    *,
    change_note: str,
    change_type: str,
    version: int,
    preflight: PreflightReport,
) -> str:
    """When change_note quality is OK *and* this row has a meaningful
    note, use it.  Otherwise fall back to ``v{n}: {change_type}`` so
    the digest still surfaces what changed, just without prose."""
    if (
        preflight.change_note_quality == "ok"
        and _is_meaningful_change_note(change_note)
    ):
        return change_note.strip()
    return f"v{version}: {change_type or 'changed'}"


def _build_cluster_membership_index(
    conn: sqlite3.Connection, *, pack: str
) -> dict[str, str]:
    """Return ``{object_id: cluster_id}`` for every clustered object
    in ``pack``.

    ``graph_clusters.member_object_ids_json`` is a JSON array of
    object_ids per cluster.  We invert it once per collect call.  At
    expected scale (hundreds of clusters, thousands of objects), this
    is cheaper than per-row ``json_each`` joins.

    Codex P2: this index is consumed by Layer 2/3 which compare
    cluster ids against the **requested pack's** crystals; building
    the index globally lets a foreign-pack cluster slip into the
    answer in multi-pack vaults.  Scope to the requested pack at
    the SQL boundary.
    """
    if _check_table_exists(conn, "graph_clusters") != "ok":
        return {}
    try:
        rows = conn.execute(
            "SELECT cluster_id, member_object_ids_json FROM graph_clusters WHERE pack = ?",
            (pack,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    index: dict[str, str] = {}
    for row in rows:
        members = _safe_json(row["member_object_ids_json"])
        if isinstance(members, list):
            for member in members:
                if isinstance(member, str) and member:
                    # First cluster wins on dup membership.
                    index.setdefault(member, row["cluster_id"])
    return index


# ---------------------------------------------------------------
# Layer 2 — connections
# ---------------------------------------------------------------


def _collect_layer2(
    conn: sqlite3.Connection,
    pack: str,
    delta: DeltaLayer,
) -> ConnectionLayer:
    touched_clusters: set[str] = {
        d.cluster_id for d in delta.new_evergreens if d.cluster_id
    } | {d.cluster_id for d in delta.updated_evergreens if d.cluster_id}

    connected_crystals: list[tuple[str, str]] = []
    if touched_clusters:
        placeholders = ",".join("?" * len(touched_clusters))
        try:
            rows = conn.execute(
                f"""
                SELECT cc.cluster_id, gc.label
                  FROM community_crystals cc
                  LEFT JOIN graph_clusters gc
                    ON gc.pack = cc.pack AND gc.cluster_id = cc.cluster_id
                 WHERE cc.pack = ?
                   AND cc.cluster_id IN ({placeholders})
                   AND cc.superseded_by_synthesized_at = ''
                """,
                (pack, *touched_clusters),
            ).fetchall()
            connected_crystals = [(r["cluster_id"], r["label"] or "") for r in rows]
        except sqlite3.OperationalError:
            connected_crystals = []

    touched_evergreen_ids = {
        d.object_id for d in delta.new_evergreens
    } | {d.object_id for d in delta.updated_evergreens}

    touched_contradictions: list[tuple[str, str]] = []
    if touched_evergreen_ids:
        try:
            rows = conn.execute(
                """
                SELECT contradiction_id, subject_key, source_object_ids_json
                  FROM contradiction_crystals
                 WHERE pack = ?
                   AND superseded_by_synthesized_at = ''
                """,
                (pack,),
            ).fetchall()
            for row in rows:
                source_ids = _safe_json(row["source_object_ids_json"])
                if isinstance(source_ids, list) and touched_evergreen_ids.intersection(
                    str(s) for s in source_ids
                ):
                    touched_contradictions.append(
                        (row["contradiction_id"], row["subject_key"] or "")
                    )
        except sqlite3.OperationalError:
            touched_contradictions = []

    recent_top: list[tuple[str, str, float, str]] = []
    # User feedback (2026-05-13): the digest body was referring to
    # crystals by hex cluster_id ("clusters: caa2903bc202, ...")
    # because we didn't pass labels to the LLM.  Join to
    # graph_clusters / contradiction_crystals to surface the
    # human-readable name alongside the id.
    try:
        rows = conn.execute(
            """
            SELECT cs.crystal_id,
                   cs.crystal_kind,
                   cs.score,
                   COALESCE(gc.label, cc.subject_key, '') AS label
              FROM crystal_scores cs
              LEFT JOIN graph_clusters gc
                ON gc.pack = cs.pack AND gc.cluster_id = cs.crystal_id
              LEFT JOIN contradiction_crystals cc
                ON cc.pack = cs.pack AND cc.contradiction_id = cs.crystal_id
             WHERE cs.pack = ?
             ORDER BY cs.score DESC
             LIMIT 5
            """,
            (pack,),
        ).fetchall()
        recent_top = [
            (
                r["crystal_id"],
                r["crystal_kind"],
                float(r["score"] or 0.0),
                (r["label"] or "").strip(),
            )
            for r in rows
        ]
    except sqlite3.OperationalError:
        recent_top = []

    return ConnectionLayer(
        connected_community_crystals=tuple(connected_crystals),
        touched_contradictions=tuple(touched_contradictions),
        recent_top_crystals=tuple(recent_top),
    )


# ---------------------------------------------------------------
# Layer 3 — pipeline state
# ---------------------------------------------------------------


def _collect_layer3(
    conn: sqlite3.Connection,
    pack: str,
    config: DigestConfig,
) -> PipelineState:
    """Aggregate: which clusters have evergreens newer than their last
    synthesis?  A cluster with a crystal but newer evergreens counts
    as unsynthesized (Codex review — stale-crystal flag)."""

    last_synthesis = ""
    try:
        row = conn.execute(
            """
            SELECT MAX(synthesized_at) FROM community_crystals
             WHERE pack = ? AND superseded_by_synthesized_at = ''
            """,
            (pack,),
        ).fetchone()
        last_synthesis = (row[0] or "") if row else ""
    except sqlite3.OperationalError:
        pass

    open_contradictions = 0
    try:
        open_contradictions = conn.execute(
            """
            SELECT COUNT(*) FROM contradiction_crystals
             WHERE pack = ? AND superseded_by_synthesized_at = ''
            """,
            (pack,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    if _check_table_exists(conn, "evergreen_revisions") != "ok":
        return PipelineState(
            unsynthesized_evergreens=0,
            last_synthesis_at=last_synthesis,
            clusters_at_threshold=(),
            open_contradictions_count=open_contradictions,
        )

    # Build per-cluster aggregates from evergreen_revisions + the
    # cluster membership index, then compare against community_crystals.
    membership = _build_cluster_membership_index(conn, pack=pack)
    if not membership:
        return PipelineState(
            unsynthesized_evergreens=0,
            last_synthesis_at=last_synthesis,
            clusters_at_threshold=(),
            open_contradictions_count=open_contradictions,
        )

    try:
        # Codex P2 sibling: pack-scope the aggregate so a multi-pack
        # vault doesn't borrow another pack's revision history when
        # counting unsynthesized evergreens.
        rows = conn.execute(
            """
            SELECT object_id, MAX(derived_at) AS latest_at
              FROM evergreen_revisions
             WHERE pack = ?
             GROUP BY object_id
            """,
            (pack,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    cluster_counts: dict[str, int] = {}
    cluster_latest_evergreen: dict[str, str] = {}
    for row in rows:
        cid = membership.get(row["object_id"], "")
        if not cid:
            continue
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1
        prior = cluster_latest_evergreen.get(cid, "")
        if row["latest_at"] and row["latest_at"] > prior:
            cluster_latest_evergreen[cid] = row["latest_at"]

    # Per-cluster latest synthesis.
    cluster_synth_at: dict[str, str] = {}
    cluster_label: dict[str, str] = {}
    try:
        for row in conn.execute(
            """
            SELECT cc.cluster_id, MAX(cc.synthesized_at) AS latest_at,
                   MAX(gc.label) AS label
              FROM community_crystals cc
              LEFT JOIN graph_clusters gc
                ON gc.pack = cc.pack AND gc.cluster_id = cc.cluster_id
             WHERE cc.pack = ? AND cc.superseded_by_synthesized_at = ''
             GROUP BY cc.cluster_id
            """,
            (pack,),
        ).fetchall():
            cluster_synth_at[row["cluster_id"]] = row["latest_at"] or ""
            cluster_label[row["cluster_id"]] = row["label"] or ""
    except sqlite3.OperationalError:
        pass

    # Pick up labels for clusters with no community_crystal.
    if _check_table_exists(conn, "graph_clusters") == "ok":
        try:
            for row in conn.execute(
                "SELECT cluster_id, label FROM graph_clusters WHERE pack = ?",
                (pack,),
            ).fetchall():
                cluster_label.setdefault(row["cluster_id"], row["label"] or "")
        except sqlite3.OperationalError:
            pass

    unsynth_total = 0
    at_threshold: list[tuple[str, str, int, bool]] = []
    for cid, count in cluster_counts.items():
        synth_at = cluster_synth_at.get(cid, "")
        latest_ev = cluster_latest_evergreen.get(cid, "")
        # Codex / CodeRabbit review: ``knowledge.db`` stores some
        # timestamps as ``...Z`` and others as ``...+00:00``; raw
        # string compare is lexicographic and silently swaps the
        # answer when the two formats meet.  Parse to datetimes and
        # compare time-correctly.  Treat unparseable / empty values
        # as "no signal", same as before.
        synth_dt = _parse_iso(synth_at)
        latest_dt = _parse_iso(latest_ev)
        stale = bool(synth_dt and latest_dt and latest_dt > synth_dt)
        # No crystal at all = also "unsynthesized".
        no_crystal = not synth_at
        if no_crystal or stale:
            unsynth_total += count
            if count >= config.cluster_threshold:
                at_threshold.append(
                    (cid, cluster_label.get(cid, ""), count, stale)
                )

    at_threshold.sort(key=lambda r: (-r[2], r[0]))
    return PipelineState(
        unsynthesized_evergreens=unsynth_total,
        last_synthesis_at=last_synthesis,
        clusters_at_threshold=tuple(at_threshold),
        open_contradictions_count=open_contradictions,
    )


# ---------------------------------------------------------------
# Input hash
# ---------------------------------------------------------------


def _compute_input_hash(inputs: DigestInputs) -> str:
    """SHA-256 over stable identifiers + window boundaries.

    Critical (codex review): the hash includes only data that
    doesn't drift with wall-clock time.  Counts, sorted id sets,
    window boundaries — yes.  Prose like "8 days ago" — never.
    """
    # Round to the minute so back-to-back mid-day regenerations don't
    # produce a fresh hash on every wall-clock microsecond tick — the
    # idempotency gate fires only when the *data* actually drifted.
    window_start_q = inputs.window_start.replace(second=0, microsecond=0)
    window_end_q = inputs.window_end.replace(second=0, microsecond=0)
    payload = {
        "window_start": _utc_iso(window_start_q),
        "window_end": _utc_iso(window_end_q),
        "pack": inputs.pack,
        "layer0_event_count": inputs.intake.intake_events_processed,
        "layer0_samples": sorted(inputs.intake.representative_samples),
        "layer1_new": sorted(d.object_id + "@v" + str(d.version) for d in inputs.delta.new_evergreens),
        "layer1_updated": sorted(d.object_id + "@v" + str(d.version) for d in inputs.delta.updated_evergreens),
        "layer2_connected": sorted(cid for cid, _ in inputs.connections.connected_community_crystals),
        "layer2_contradictions": sorted(cid for cid, _ in inputs.connections.touched_contradictions),
        "layer3_unsynth": inputs.pipeline_state.unsynthesized_evergreens,
        "layer3_last_synth": inputs.pipeline_state.last_synthesis_at,
        "layer3_at_threshold": sorted(cid for cid, _, _, _ in inputs.pipeline_state.clusters_at_threshold),
        "layer3_open_contradictions": inputs.pipeline_state.open_contradictions_count,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _safe_json(blob: Any) -> Any:
    if not blob:
        return None
    try:
        return json.loads(blob)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _utc_iso(dt: datetime) -> str:
    """Normalize a tz-aware datetime to a UTC ISO string for SQL binding.

    Stored timestamps in ``knowledge.db`` are UTC (some as
    ``...+00:00``, some as ``...Z``); a string-comparison against
    an operator-local ISO like ``...-07:00`` is lexicographic, not
    time-correct.  Every SQL bind site goes through this helper.
    """
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(raw: str) -> datetime | None:
    """Permissive ISO-8601 parser.  Returns None on failure."""
    raw = (raw or "").strip()
    if not raw:
        return None
    # Z → +00:00 for fromisoformat compatibility.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _tz_display_name(tz: Any) -> str:
    """Best-effort tz name for the digest frontmatter.

    Stdlib :class:`zoneinfo.ZoneInfo` exposes ``key``; ``tzlocal``
    may return a pytz shim whose ``zone`` accessor triggers a
    deprecation warning.  Try ``key`` first, fall back to ``str(tz)``.
    """
    name = getattr(tz, "key", None)
    if isinstance(name, str) and name:
        return name
    try:
        return str(tz)
    except Exception:  # noqa: BLE001
        return "UTC"


def _empty_inputs(
    *,
    pack: str,
    window_start: datetime,
    window_end: datetime,
    tz_name: str,
    config: DigestConfig,
    preflight: PreflightReport,
) -> DigestInputs:
    return DigestInputs(
        pack=pack,
        window_start=window_start,
        window_end=window_end,
        tz_name=tz_name,
        config=config,
        preflight=preflight,
        intake=IntakeLayer(0, (), (), ()),
        delta=DeltaLayer((), ()),
        connections=ConnectionLayer((), (), ()),
        pipeline_state=PipelineState(0, "", (), 0),
    )


__all__ = [
    "ConnectionLayer",
    "DeltaLayer",
    "DigestInputs",
    "EvergreenDelta",
    "IntakeLayer",
    "PipelineState",
    "PreflightReport",
    "collect_digest_inputs",
]
