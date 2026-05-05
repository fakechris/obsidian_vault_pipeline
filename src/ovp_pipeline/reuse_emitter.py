"""
Helpers shared by Phase 32 reuse-event consumer sites.

Every canonical object surfaced to a downstream consumer (query, briefing,
export, truth_api, prompt assembly, compiled view) routes through here so the
``trusted`` computation has a single home:

  trusted = evidence_present AND provenance_clean

  evidence_present : >=1 claim_evidence row exists for the object.
  provenance_clean : object exists in ``objects`` AND no audit_events of
                     event_type='broken_link' for this slug in the last
                     30 days.

The functions do one batched DB read per call (not per object). Callers pass
the surface name and an opaque ``consumer_ref`` (e.g. the question text for
queries, the export target for compiled views).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .event_emitter import emit
from .runtime import VaultLayout


_BROKEN_LINK_WINDOW_DAYS = 30


def extract_cited_slugs(evidence_payload: dict[str, Any]) -> list[str]:
    """Pull canonical-looking slugs out of a ``build_evidence_payload`` result.

    Order-preserving, deduplicated. Reads ``identity_evidence[*].entry_slug``
    and ``retrieval_evidence[*].slug``.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for entry in evidence_payload.get("identity_evidence", []) or []:
        slug = str((entry or {}).get("entry_slug") or "")
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    for entry in evidence_payload.get("retrieval_evidence", []) or []:
        slug = str((entry or {}).get("slug") or "")
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def collect_object_ids(payload: object) -> list[str]:
    """Walk a JSON-shaped payload and return distinct ``object_id`` string values.

    Order-preserving. Used by surfaces (export, view_models, briefing) that
    package canonical objects inside larger structures and need to emit one
    reuse event per object referenced.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            value = node.get("object_id")
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                ordered.append(value)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return ordered


def _broken_link_cutoff_text() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_BROKEN_LINK_WINDOW_DAYS)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_by_slug(
    db_path: Path,
    pack: str,
    slugs: list[str],
) -> tuple[dict[str, tuple[str, str]], set[str], set[str]]:
    """Return (slug -> (object_id, object_kind), evidence_slugs, broken_slugs)."""
    if not slugs or not db_path.exists():
        return {}, set(), set()

    placeholders = ",".join("?" for _ in slugs)
    cutoff = _broken_link_cutoff_text()
    object_map: dict[str, tuple[str, str]] = {}
    evidence_slugs: set[str] = set()
    broken_slugs: set[str] = set()

    with sqlite3.connect(db_path) as conn:
        object_rows = conn.execute(
            f"""
            SELECT source_slug, object_id, object_kind
            FROM objects
            WHERE pack = ? AND source_slug IN ({placeholders})
            """,
            (pack, *slugs),
        ).fetchall()
        for source_slug, object_id, object_kind in object_rows:
            object_map[str(source_slug)] = (str(object_id), str(object_kind))

        if object_map:
            object_ids = [oid for oid, _ in object_map.values()]
            evid_placeholders = ",".join("?" for _ in object_ids)
            evidence_rows = conn.execute(
                f"""
                SELECT DISTINCT claims.object_id
                FROM claim_evidence
                JOIN claims ON claims.pack = claim_evidence.pack
                           AND claims.claim_id = claim_evidence.claim_id
                WHERE claims.pack = ? AND claims.object_id IN ({evid_placeholders})
                """,
                (pack, *object_ids),
            ).fetchall()
            cited_object_ids = {str(row[0]) for row in evidence_rows}
            for source_slug, (object_id, _kind) in object_map.items():
                if object_id in cited_object_ids:
                    evidence_slugs.add(source_slug)

        broken_rows = conn.execute(
            f"""
            SELECT DISTINCT slug
            FROM audit_events
            WHERE event_type = 'broken_link'
              AND timestamp >= ?
              AND slug IN ({placeholders})
            """,
            (cutoff, *slugs),
        ).fetchall()
        broken_slugs = {str(row[0]) for row in broken_rows if row[0]}

    return object_map, evidence_slugs, broken_slugs


def _resolve_by_object_id(
    db_path: Path,
    pack: str,
    object_ids: list[str],
) -> tuple[dict[str, tuple[str, str]], set[str], set[str]]:
    """Return (object_id -> (source_slug, object_kind), evidence_object_ids, broken_object_ids)."""
    if not object_ids or not db_path.exists():
        return {}, set(), set()

    placeholders = ",".join("?" for _ in object_ids)
    cutoff = _broken_link_cutoff_text()
    object_map: dict[str, tuple[str, str]] = {}
    evidence_object_ids: set[str] = set()
    broken_object_ids: set[str] = set()

    with sqlite3.connect(db_path) as conn:
        object_rows = conn.execute(
            f"""
            SELECT object_id, source_slug, object_kind
            FROM objects
            WHERE pack = ? AND object_id IN ({placeholders})
            """,
            (pack, *object_ids),
        ).fetchall()
        for object_id, source_slug, object_kind in object_rows:
            object_map[str(object_id)] = (str(source_slug), str(object_kind))

        if object_map:
            evid_placeholders = ",".join("?" for _ in object_map)
            evidence_rows = conn.execute(
                f"""
                SELECT DISTINCT claims.object_id
                FROM claim_evidence
                JOIN claims ON claims.pack = claim_evidence.pack
                           AND claims.claim_id = claim_evidence.claim_id
                WHERE claims.pack = ? AND claims.object_id IN ({evid_placeholders})
                """,
                (pack, *object_map.keys()),
            ).fetchall()
            evidence_object_ids = {str(row[0]) for row in evidence_rows}

            slugs = {source_slug for source_slug, _ in object_map.values() if source_slug}
            if slugs:
                slug_placeholders = ",".join("?" for _ in slugs)
                broken_rows = conn.execute(
                    f"""
                    SELECT DISTINCT slug
                    FROM audit_events
                    WHERE event_type = 'broken_link'
                      AND timestamp >= ?
                      AND slug IN ({slug_placeholders})
                    """,
                    (cutoff, *slugs),
                ).fetchall()
                broken_slug_set = {str(row[0]) for row in broken_rows if row[0]}
                for object_id, (source_slug, _kind) in object_map.items():
                    if source_slug in broken_slug_set:
                        broken_object_ids.add(object_id)

    return object_map, evidence_object_ids, broken_object_ids


def _write_event(
    vault_dir: Path | str,
    *,
    pack: str,
    object_id: str,
    object_kind: str,
    source_slug: str,
    surface: str,
    consumer_ref: str,
    evidence_present: bool,
    provenance_clean: bool,
    session_id: str | None,
    extra_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    trusted = evidence_present and provenance_clean
    payload: dict[str, Any] = {
        "object_id": object_id,
        "object_kind": object_kind,
        "surface": surface,
        "consumer_ref": consumer_ref,
        "evidence_present": int(evidence_present),
        "provenance_clean": int(provenance_clean),
        "trusted": int(trusted),
        "source_slug": source_slug,
    }
    if extra_payload:
        payload.update(extra_payload)
    return emit(
        vault_dir,
        "reuse-events.jsonl",
        "trusted_reuse_event",
        payload,
        session_id=session_id,
        pack=pack,
    )


def emit_reuse_events(
    vault_dir: Path | str,
    *,
    pack: str,
    slugs: Iterable[str],
    surface: str,
    consumer_ref: str = "",
    session_id: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Resolve ``slugs`` to canonical objects, emit one event per resolved slug.

    Returns the list of events actually written (skipping slugs that don't
    resolve to a canonical object in this pack). The DB is read once.
    """
    layout = VaultLayout.from_vault(vault_dir)
    db_path = layout.knowledge_db
    slug_list: list[str] = []
    seen_slugs: set[str] = set()
    for slug in slugs:
        text = str(slug or "")
        if not text or text in seen_slugs:
            continue
        seen_slugs.add(text)
        slug_list.append(text)
    if not slug_list:
        return []

    object_map, evidence_slugs, broken_slugs = _resolve_by_slug(db_path, pack, slug_list)
    if not object_map:
        return []

    emitted: list[dict[str, Any]] = []
    for slug in slug_list:
        resolved = object_map.get(slug)
        if not resolved:
            continue
        object_id, object_kind = resolved
        emitted.append(
            _write_event(
                vault_dir,
                pack=pack,
                object_id=object_id,
                object_kind=object_kind,
                source_slug=slug,
                surface=surface,
                consumer_ref=consumer_ref,
                evidence_present=slug in evidence_slugs,
                provenance_clean=slug not in broken_slugs,
                session_id=session_id,
                extra_payload=extra_payload,
            )
        )
    return emitted


def emit_crystal_reuse_events(
    vault_dir: Path | str,
    *,
    pack: str,
    crystals: Iterable[tuple[str, str]],
    surface: str,
    consumer_ref: str = "",
    session_id: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Emit reuse events for synthesized crystals.

    Crystals don't live in the ``objects`` table — they live in
    ``community_crystals`` / ``contradiction_crystals`` — so the
    standard ``emit_reuse_events*`` resolvers can't reach them.
    Without a producer, ``crystal_scoring._reuse_recency_signal``
    permanently reads ``reuse_events`` rows that never get written
    and the ``reuse_recency_norm`` signal stays cold-zero in
    production regardless of actual user activity.

    ``crystals`` is an iterable of ``(crystal_kind, crystal_id)``
    tuples where ``crystal_kind`` is one of the
    ``_CRYSTAL_REUSE_KINDS`` values
    (``community_crystal`` / ``contradiction_crystal``).  We
    write the event directly without any objects-table resolution
    so this function works even before any of the
    ``crystal_scoring`` plumbing has run.

    ``evidence_present`` and ``provenance_clean`` are pinned to
    1/1 — every crystal currently in the table is by definition
    a "trusted" synthesis (LLM-produced from canonical objects with
    full lineage) so the credibility-of-the-feedback signal is on
    for the same reason a query that hits a green-circle evergreen
    counts as trusted reuse.
    """
    valid_kinds = {"community_crystal", "contradiction_crystal"}
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str]] = []
    for kind, crystal_id in crystals:
        kind_str = str(kind or "")
        id_str = str(crystal_id or "")
        if kind_str not in valid_kinds or not id_str:
            continue
        key = (kind_str, id_str)
        if key in seen:
            continue
        seen.add(key)
        rows.append(key)
    if not rows:
        return []

    emitted: list[dict[str, Any]] = []
    for kind_str, id_str in rows:
        emitted.append(
            _write_event(
                vault_dir,
                pack=pack,
                object_id=id_str,
                object_kind=kind_str,
                source_slug="",
                surface=surface,
                consumer_ref=consumer_ref,
                evidence_present=True,
                provenance_clean=True,
                session_id=session_id,
                extra_payload=extra_payload,
            )
        )
    return emitted


def emit_reuse_events_for_object_ids(
    vault_dir: Path | str,
    *,
    pack: str,
    object_ids: Iterable[str],
    surface: str,
    consumer_ref: str = "",
    session_id: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Object-id-keyed sister of :func:`emit_reuse_events`.

    Used by surfaces that already hold canonical ``object_id`` values
    (truth_api detail/neighborhood, view_models payloads, exports). Skips
    object ids that don't resolve to a row in this pack.
    """
    layout = VaultLayout.from_vault(vault_dir)
    db_path = layout.knowledge_db
    id_list: list[str] = []
    seen_ids: set[str] = set()
    for oid in object_ids:
        text = str(oid or "")
        if not text or text in seen_ids:
            continue
        seen_ids.add(text)
        id_list.append(text)
    if not id_list:
        return []

    object_map, evidence_object_ids, broken_object_ids = _resolve_by_object_id(
        db_path, pack, id_list
    )
    if not object_map:
        return []

    emitted: list[dict[str, Any]] = []
    for object_id in id_list:
        resolved = object_map.get(object_id)
        if not resolved:
            continue
        source_slug, object_kind = resolved
        emitted.append(
            _write_event(
                vault_dir,
                pack=pack,
                object_id=object_id,
                object_kind=object_kind,
                source_slug=source_slug,
                surface=surface,
                consumer_ref=consumer_ref,
                evidence_present=object_id in evidence_object_ids,
                provenance_clean=object_id not in broken_object_ids,
                session_id=session_id,
                extra_payload=extra_payload,
            )
        )
    return emitted
