"""Phase 35 — promote semantic relation candidates into the truth store.

Bridges :mod:`extraction.semantic_relations` (which produces JSON candidates)
and :mod:`truth_store` (which holds the canonical ``relations`` and
``graph_edges`` rows). Each candidate is run through
``promotion_policy.evaluate_relation``; auto-lane writes both a ``relations``
row (with the Phase 33 evidence columns) and a ``graph_edges`` row.

The CLI surface is :mod:`commands.promote` (``ovp-promote relations``).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .derived.paths import review_queue_path
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
