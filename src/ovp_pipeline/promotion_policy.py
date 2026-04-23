"""Phase 34 — promotion policy engine.

Replaces the inline OR rule at ``promote_candidates.review_candidates`` with a
pack-driven policy. Two evaluators:

* ``evaluate_concept`` — concept candidate → canonical
* ``evaluate_workspace`` — agent-owned draft → accepted-state file

Both return a serializable :class:`PolicyDecision` dataclass so the result can
flow through audit events and the upcoming MCP wrapper without a second
serialization pass.

The default-knowledge pack ships with ``legacy_or_rule=True`` to preserve the
historical ``source_count >= 2 or evidence_count >= 3`` behavior bit-for-bit.
Strict packs (research-tech) leave the flag off and rely on the structured
fields in :class:`AutoPromoteRule`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .concept_registry import ConceptEntry, ConceptRegistry
from .packs.base import BaseDomainPack, PromotionPolicySpec
from .state_lifecycle import State


LANE_AUTO = "auto"
LANE_ESCALATE = "escalate"
LANE_REJECT = "reject"
LANE_HOLD = "hold"

_VALID_LANES = frozenset({LANE_AUTO, LANE_ESCALATE, LANE_REJECT, LANE_HOLD})


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of a single policy evaluation.

    ``lane`` is the action category. ``reason_code`` is a stable identifier so
    downstream tooling (review queue UI, MCP) can group decisions without
    parsing free text. ``blocking_facts`` enumerates the structured reasons —
    consumed by escalation queues to render review prompts.
    """

    lane: str
    reason_code: str
    blocking_facts: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lane not in _VALID_LANES:
            raise ValueError(f"Invalid lane '{self.lane}'")


# ---------------------------------------------------------------------------
# Concept lane evaluation
# ---------------------------------------------------------------------------


def collect_pack_signals(
    db_path: Path | None,
    *,
    pack_name: str,
    candidates_dir: Path | None = None,
) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """Bulk-load (slug → evidence_kinds, slugs with open contradiction).

    Both signals are inputs to :func:`evaluate_concept`'s strict path. Without
    them, every required ``evidence_kind`` is treated as missing — which makes
    strict packs (research-tech) escalate every candidate regardless of actual
    evidence. Centralizing the queries here so doctor / ``ovp-promote run`` /
    ``review_candidates`` all ask the DB the same way.

    The ``kinds_map`` is keyed by slug. For canonical objects the slug is the
    ``objects.object_id`` and the kinds come from ``claim_evidence``. For
    pre-promotion candidates the slug is the candidate's filename stem and the
    kind is implicitly ``page_summary`` — the candidate file *is* a page-level
    summary written by ``write_candidate_file``. Without this seed, strict
    packs that require a ``page_summary`` evidence kind would never auto-
    promote any candidate, because candidates don't yet appear in the
    canonical objects table the projection writes from.

    Returns ``({}, frozenset())`` when the DB is missing or the schema isn't
    materialized yet (zero-state vaults).
    """
    kinds_map: dict[str, set[str]] = {}
    disputed: set[str] = set()

    if db_path is not None and Path(db_path).exists():
        try:
            with sqlite3.connect(db_path) as conn:
                for object_id, kind in conn.execute(
                    "SELECT c.object_id, ce.evidence_kind "
                    "FROM claim_evidence ce "
                    "JOIN claims c ON c.pack = ce.pack AND c.claim_id = ce.claim_id "
                    "WHERE c.pack = ?",
                    (pack_name,),
                ):
                    if not object_id or not kind:
                        continue
                    kinds_map.setdefault(object_id, set()).add(kind)

                for object_id, claim_id in conn.execute(
                    "SELECT object_id, claim_id FROM claims WHERE pack = ?",
                    (pack_name,),
                ):
                    if not object_id or not claim_id:
                        continue
                    hit = conn.execute(
                        """
                        SELECT 1 FROM contradictions
                        WHERE pack = ? AND status = 'open'
                          AND (positive_claim_ids_json LIKE ?
                            OR negative_claim_ids_json LIKE ?)
                        LIMIT 1
                        """,
                        (pack_name, f'%"{claim_id}"%', f'%"{claim_id}"%'),
                    ).fetchone()
                    if hit:
                        disputed.add(object_id)
        except sqlite3.OperationalError:
            kinds_map.clear()
            disputed.clear()

    if candidates_dir is not None and Path(candidates_dir).exists():
        for entry in Path(candidates_dir).glob("*.md"):
            if not entry.is_file():
                continue
            kinds_map.setdefault(entry.stem, set()).add("page_summary")

    return {oid: frozenset(kinds) for oid, kinds in kinds_map.items()}, frozenset(disputed)


def independent_source_count(
    entry: ConceptEntry,
    registry: ConceptRegistry | None = None,
) -> int:
    """Best-effort estimate of distinct sources backing a candidate.

    Plan §5.14 risk #1: ``ConceptEntry`` only persists a precomputed
    ``source_count`` (no sighting tuples). Until the registry adds sighting
    persistence, fall back to that count.
    """
    _ = registry  # reserved for the sighting-tuple upgrade path
    return int(getattr(entry, "source_count", 0) or 0)


def _legacy_or_lane(entry: ConceptEntry) -> tuple[str, str, tuple[str, ...]]:
    """Reproduces the pre-Phase-34 OR rule used by ``default-knowledge``.

    Only ``LANE_AUTO`` (promote) or ``LANE_HOLD`` (keep_as_candidate) — the
    legacy code never auto-rejected, never auto-escalated.
    """
    if int(entry.source_count) >= 2 or int(entry.evidence_count) >= 3:
        return LANE_AUTO, "legacy_or_rule", ()
    return LANE_HOLD, "legacy_or_rule_below_threshold", (
        f"source_count={entry.source_count}",
        f"evidence_count={entry.evidence_count}",
    )


def _strict_lane(
    entry: ConceptEntry,
    *,
    policy: PromotionPolicySpec,
    registry: ConceptRegistry | None,
    has_open_contradiction: bool,
    evidence_kinds: frozenset[str],
) -> tuple[str, str, tuple[str, ...]]:
    auto = policy.auto_promote
    facts: list[str] = []

    independent = independent_source_count(entry, registry)
    if independent < auto.require_independent_sources:
        facts.append(
            f"independent_sources={independent}<{auto.require_independent_sources}"
        )
    if auto.require_evidence_kinds:
        missing = [kind for kind in auto.require_evidence_kinds if kind not in evidence_kinds]
        if missing:
            facts.append(f"missing_evidence_kinds={','.join(missing)}")
    if auto.require_no_open_contradiction and has_open_contradiction:
        facts.append("open_contradiction")

    reject = policy.reject
    if entry.evidence_count < reject.min_evidence_floor:
        return (
            LANE_REJECT,
            "below_evidence_floor",
            (f"evidence_count={entry.evidence_count}<{reject.min_evidence_floor}",),
        )

    if not facts:
        return LANE_AUTO, "policy_satisfied", ()

    escalate = policy.escalate_to_workbench
    if (
        (escalate.on_partial_evidence and "missing_evidence_kinds" in " ".join(facts))
        or (escalate.on_disputed and "open_contradiction" in facts)
        or (escalate.on_unverified_evidence and "independent_sources" in " ".join(facts))
    ):
        return LANE_ESCALATE, "escalated", tuple(facts)
    return LANE_HOLD, "held_for_more_signal", tuple(facts)


def evaluate_concept(
    entry: ConceptEntry,
    *,
    pack: BaseDomainPack,
    registry: ConceptRegistry | None = None,
    has_open_contradiction: bool = False,
    evidence_kinds: frozenset[str] | None = None,
) -> PolicyDecision:
    """Decide the promotion lane for a single concept candidate.

    ``evidence_kinds`` is the set of distinct ``claim_evidence.evidence_kind``
    values currently backing the candidate (callers compute it from the
    knowledge.db). Pass ``None`` if unknown — the strict path then treats
    every required kind as missing.
    """
    policy = pack.promotion_policy()
    if policy.auto_promote.legacy_or_rule:
        lane, reason, facts = _legacy_or_lane(entry)
    else:
        lane, reason, facts = _strict_lane(
            entry,
            policy=policy,
            registry=registry,
            has_open_contradiction=has_open_contradiction,
            evidence_kinds=evidence_kinds or frozenset(),
        )
    return PolicyDecision(
        lane=lane,
        reason_code=reason,
        blocking_facts=facts,
        payload={"slug": entry.slug, "pack": pack.name},
    )


# ---------------------------------------------------------------------------
# Workspace lane evaluation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Relation lane evaluation (Phase 35)
# ---------------------------------------------------------------------------


def evaluate_relation(
    candidate: Any,
    *,
    pack: BaseDomainPack,
    has_open_contradiction: bool = False,
) -> PolicyDecision:
    """Decide the promotion lane for a single ``SemanticRelationCandidate``.

    Permissive packs auto-pass any well-formed candidate. Strict packs apply
    ``evidence_requirements.relation_must_have``: each declared field must be
    populated. Auto-promotes when complete; escalates on partial evidence;
    rejects below the evidence floor.
    """
    requirements = pack.evidence_requirements()
    payload = {
        "pack": pack.name,
        "relation_type": getattr(candidate, "relation_type", ""),
        "source_object_id": getattr(candidate, "source_object_id", ""),
        "target_object_id": getattr(candidate, "target_object_id", ""),
    }

    if not requirements.relation_must_have:
        return PolicyDecision(
            lane=LANE_AUTO,
            reason_code="permissive_pack",
            blocking_facts=(),
            payload=payload,
        )

    # The relations table column is ``evidence_source_slug``; the candidate
    # dataclass calls the same data ``source_slug``. Translate so pack
    # requirements can use either name.
    field_aliases = {"evidence_source_slug": "source_slug"}
    facts: list[str] = []
    for field_name in requirements.relation_must_have:
        attr = field_aliases.get(field_name, field_name)
        value = getattr(candidate, attr, "")
        if isinstance(value, str):
            value = value.strip()
        if not value:
            facts.append(f"missing_field={field_name}")

    if has_open_contradiction:
        facts.append("open_contradiction")

    policy = pack.promotion_policy()
    if not facts:
        return PolicyDecision(
            lane=LANE_AUTO,
            reason_code="policy_satisfied",
            blocking_facts=(),
            payload=payload,
        )
    if policy.escalate_to_workbench.on_partial_evidence:
        return PolicyDecision(
            lane=LANE_ESCALATE,
            reason_code="escalated",
            blocking_facts=tuple(facts),
            payload=payload,
        )
    return PolicyDecision(
        lane=LANE_REJECT,
        reason_code="below_evidence_floor",
        blocking_facts=tuple(facts),
        payload=payload,
    )


def evaluate_workspace(
    draft_path: Path,
    target_path: Path,
    *,
    pack: BaseDomainPack,
    target_state: State | None = None,
) -> PolicyDecision:
    """Decide whether an agent draft may be promoted to an accepted-state file.

    v1 is intentionally permissive: as long as the source path resolves and
    the target zone is declared accepted (or the pack runs in legacy
    permissive mode), promotion auto-passes. Phase 34.E adds the actual zone
    enforcement at the write sites; this hook lets ``ovp-promote workspace``
    and the lint rule share a decision shape.
    """
    zones = pack.workspace_zones()
    payload = {
        "draft": str(draft_path),
        "target": str(target_path),
        "pack": pack.name,
        "target_state": target_state.value if isinstance(target_state, State) else None,
    }

    if not zones.accepted:
        return PolicyDecision(
            lane=LANE_AUTO,
            reason_code="permissive_pack",
            blocking_facts=(),
            payload=payload,
        )

    facts: list[str] = []
    if not draft_path.exists():
        facts.append("draft_missing")
    if facts:
        return PolicyDecision(
            lane=LANE_REJECT,
            reason_code="invalid_draft",
            blocking_facts=tuple(facts),
            payload=payload,
        )
    return PolicyDecision(
        lane=LANE_AUTO,
        reason_code="workspace_promotion_allowed",
        blocking_facts=(),
        payload=payload,
    )
