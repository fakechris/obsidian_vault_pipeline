"""Phase 35 — promote semantic relation candidates into the truth store.

Bridges :mod:`extraction.semantic_relations` (which produces JSON candidates)
and :mod:`truth_store` (which holds the canonical ``relations`` and
``graph_edges`` rows). Each candidate is run through
``promotion_policy.evaluate_relation``; auto-lane writes both a ``relations``
row (with the Phase 33 evidence columns) and a ``graph_edges`` row.

Durability: ``rebuild_knowledge_index`` recreates ``relations`` from the pack
projection, which would otherwise drop every row written here. Each AUTO
promotion appends one ``relation_promoted`` event to
``60-Logs/relation-promotions.jsonl`` carrying the full row, and
:func:`replay_relation_promotions` re-applies them on rebuild.

The CLI surface is :mod:`commands.promote` (``ovp-promote relations``).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .derived.paths import review_queue_path
from .event_emitter import emit, iter_for_index
from .extraction.semantic_relations import (
    SemanticRelationCandidate,
    candidate_subject,
    load_candidates,
)
from .knowledge_index import ensure_knowledge_db_current
from .packs.base import BaseDomainPack
from .promotion_audit import emit_promotion
from .promotion_policy import LANE_AUTO, LANE_ESCALATE, LANE_REJECT, evaluate_relation
from .runtime import VaultLayout
from .state_lifecycle import State
from .truth_store import EVIDENCE_STATUS_UNVERIFIED


RELATION_PROMOTIONS_LOG = "relation-promotions.jsonl"


@dataclass
class RelationPromotionReport:
    promoted: list[SemanticRelationCandidate] = field(default_factory=list)
    escalated: list[tuple[SemanticRelationCandidate, tuple[str, ...]]] = field(default_factory=list)
    rejected: list[tuple[SemanticRelationCandidate, tuple[str, ...]]] = field(default_factory=list)

    def lane_counts(self) -> dict[str, int]:
        return {
            "auto": len(self.promoted),
            "escalate": len(self.escalated),
            "reject": len(self.rejected),
        }


def _edge_id(candidate: SemanticRelationCandidate) -> str:
    """Stable id derived from the (source, type, target, source_slug) tuple."""
    payload = "|".join(
        (
            candidate.source_object_id,
            candidate.relation_type,
            candidate.target_object_id,
            candidate.source_slug,
        )
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _ensure_relation_row(
    conn: sqlite3.Connection,
    candidate: SemanticRelationCandidate,
) -> None:
    conn.execute(
        """
        INSERT INTO relations (
          pack, source_object_id, target_object_id, relation_type,
          evidence_source_slug, quote_text, locator, content_hash, retrieval_context,
          status, verified_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.pack,
            candidate.source_object_id,
            candidate.target_object_id,
            candidate.relation_type,
            candidate.source_slug,
            candidate.evidence_quote,
            candidate.locator,
            candidate.content_hash,
            candidate.retrieval_context,
            EVIDENCE_STATUS_UNVERIFIED,
            "",
        ),
    )


def _ensure_graph_edge_row(
    conn: sqlite3.Connection,
    candidate: SemanticRelationCandidate,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO graph_edges (
          pack, edge_id, source_object_id, target_object_id, edge_kind,
          weight, evidence_source_slug
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.pack,
            _edge_id(candidate),
            candidate.source_object_id,
            candidate.target_object_id,
            candidate.relation_type,
            float(candidate.confidence or 1.0),
            candidate.source_slug,
        ),
    )


def _emit_relation_promoted(
    layout: VaultLayout,
    candidate: SemanticRelationCandidate,
) -> None:
    """Append the full row payload so :func:`replay_relation_promotions`
    can rehydrate after the projection clears the table on rebuild."""
    emit(
        layout.vault_dir,
        RELATION_PROMOTIONS_LOG,
        "relation_promoted",
        {
            "pack": candidate.pack,
            "source_object_id": candidate.source_object_id,
            "target_object_id": candidate.target_object_id,
            "relation_type": candidate.relation_type,
            "evidence_source_slug": candidate.source_slug,
            "quote_text": candidate.evidence_quote,
            "locator": candidate.locator,
            "content_hash": candidate.content_hash,
            "retrieval_context": candidate.retrieval_context,
            "edge_weight": float(candidate.confidence or 1.0),
            "edge_id": _edge_id(candidate),
        },
        pack=candidate.pack,
    )


def _latest_promotion_per_key(
    events: Iterable[dict[str, Any]],
) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    """Collapse N ``relation_promoted`` events per natural key into the latest.

    JSONL is append-only and chronological, so iterating the log to overwrite
    per-key entries leaves the most-recent event in the dict. This makes
    re-promotion-with-updated-metadata survive rebuild — without this step the
    first event would win and any later ``locator``/``content_hash`` updates
    would silently revert. Mirrors :func:`evidence_replay._latest_per_key`.
    """
    latest: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for event in events:
        key = (
            str(event.get("pack") or ""),
            str(event.get("source_object_id") or ""),
            str(event.get("target_object_id") or ""),
            str(event.get("relation_type") or ""),
            str(event.get("evidence_source_slug") or ""),
        )
        latest[key] = event
    return latest


def replay_relation_promotions(
    conn: sqlite3.Connection,
    layout: VaultLayout,
    *,
    pack_name: str,
) -> int:
    """Re-apply every ``relation_promoted`` event for ``pack_name``.

    Called from :func:`knowledge_index.rebuild_knowledge_index` after the
    projection has finished inserting its (link-derived) relations. The
    natural key of ``relations`` is ``(pack, source, target, type,
    evidence_source_slug)``; we collapse multiple events per key to the latest
    (last-event-wins) so re-promotions with updated evidence metadata survive
    rebuild, then dedupe against rows already present so a promotion that
    happens to overlap a wikilink-derived relation does not produce a second
    row. ``graph_edges`` uses ``INSERT OR REPLACE`` keyed by the deterministic
    ``edge_id``.
    """
    pack_events = (
        ev
        for ev in iter_for_index(layout, RELATION_PROMOTIONS_LOG)
        if ev.get("event_type") == "relation_promoted" and ev.get("pack") == pack_name
    )
    latest = _latest_promotion_per_key(pack_events)
    if not latest:
        return 0

    existing_keys: set[tuple[str, str, str, str, str]] = set()
    for row in conn.execute(
        "SELECT pack, source_object_id, target_object_id, relation_type, evidence_source_slug "
        "FROM relations WHERE pack = ?",
        (pack_name,),
    ):
        existing_keys.add(tuple(str(value or "") for value in row))  # type: ignore[arg-type]

    inserted = 0
    for key, event in latest.items():
        if key in existing_keys:
            continue
        conn.execute(
            """
            INSERT INTO relations (
              pack, source_object_id, target_object_id, relation_type,
              evidence_source_slug, quote_text, locator, content_hash,
              retrieval_context, status, verified_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *key,
                str(event.get("quote_text") or ""),
                str(event.get("locator") or ""),
                str(event.get("content_hash") or ""),
                str(event.get("retrieval_context") or ""),
                EVIDENCE_STATUS_UNVERIFIED,
                "",
            ),
        )
        existing_keys.add(key)
        edge_id = str(event.get("edge_id") or "")
        if edge_id:
            conn.execute(
                """
                INSERT OR REPLACE INTO graph_edges (
                  pack, edge_id, source_object_id, target_object_id, edge_kind,
                  weight, evidence_source_slug
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key[0],
                    edge_id,
                    key[1],
                    key[2],
                    key[3],
                    float(event.get("edge_weight") or 1.0),
                    key[4],
                ),
            )
        inserted += 1
    return inserted


def _archive_candidate(
    layout: VaultLayout,
    candidate: SemanticRelationCandidate,
    facts: tuple[str, ...],
) -> Path:
    """Persist a rejected candidate so the doctor and lint can audit it later."""
    target = layout.derived_dir / "rejected-relations"
    target.mkdir(parents=True, exist_ok=True)
    name = f"{candidate.source_object_id}__{candidate.relation_type}__{candidate.target_object_id}.json"
    path = target / name
    payload = candidate.to_dict()
    payload["rejection_facts"] = list(facts)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _queue_path_for(
    layout: VaultLayout,
    candidate: SemanticRelationCandidate,
    queue_name: str,
) -> Path:
    return review_queue_path(
        layout,
        queue_name=queue_name,
        subject=candidate_subject(candidate),
    )


def promote_candidates(
    candidates: Iterable[SemanticRelationCandidate],
    *,
    pack: BaseDomainPack,
    layout: VaultLayout,
    actor: str = "ovp-promote relations",
    queue_name: str = "semantic-relations",
) -> RelationPromotionReport:
    """Apply pack policy to each candidate and write auto-lane rows to truth.

    The ``relations`` table has no unique constraint on
    ``(pack, source, target, type)``, so duplicate inserts cannot be deduped at
    the SQL layer. Instead, after a successful AUTO promotion (or REJECT
    archival) the originating queue file is deleted so the next
    ``ovp-promote relations`` run does not re-process it. ``graph_edges`` uses
    ``INSERT OR REPLACE`` keyed by a deterministic edge id, so the same
    candidate stays one edge regardless.
    """
    report = RelationPromotionReport()
    db_path = ensure_knowledge_db_current(layout.vault_dir)
    conn = sqlite3.connect(db_path)
    auto_to_clean: list[SemanticRelationCandidate] = []
    reject_to_clean: list[SemanticRelationCandidate] = []

    try:
        for candidate in candidates:
            decision = evaluate_relation(candidate, pack=pack)
            if decision.lane == LANE_AUTO:
                _ensure_relation_row(conn, candidate)
                _ensure_graph_edge_row(conn, candidate)
                _emit_relation_promoted(layout, candidate)
                emit_promotion(
                    layout.vault_dir,
                    pack=pack.name,
                    from_state=State.CANDIDATE,
                    to_state=State.CANONICAL,
                    target_path=layout.knowledge_db,
                    actor=actor,
                    reason="relation_promoted",
                    payload={
                        "relation_type": candidate.relation_type,
                        "source_object_id": candidate.source_object_id,
                        "target_object_id": candidate.target_object_id,
                        "source_slug": candidate.source_slug,
                    },
                )
                report.promoted.append(candidate)
                auto_to_clean.append(candidate)
            elif decision.lane == LANE_ESCALATE:
                report.escalated.append((candidate, decision.blocking_facts))
            elif decision.lane == LANE_REJECT:
                _archive_candidate(layout, candidate, decision.blocking_facts)
                report.rejected.append((candidate, decision.blocking_facts))
                reject_to_clean.append(candidate)
        conn.commit()
    finally:
        conn.close()

    # Drop queue files only after the DB commit succeeds so a mid-run crash
    # leaves the queue intact for retry.
    for candidate in auto_to_clean + reject_to_clean:
        queue_file = _queue_path_for(layout, candidate, queue_name)
        try:
            queue_file.unlink()
        except FileNotFoundError:
            pass

    return report


def promote_review_queue(
    layout: VaultLayout,
    *,
    pack: BaseDomainPack,
    queue_name: str = "semantic-relations",
) -> RelationPromotionReport:
    """Convenience: load every candidate file in the queue and promote it."""
    candidates = load_candidates(layout, queue_name=queue_name)
    return promote_candidates(candidates, pack=pack, layout=layout, queue_name=queue_name)
