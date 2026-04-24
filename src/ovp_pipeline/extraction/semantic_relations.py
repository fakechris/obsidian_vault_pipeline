"""Phase 35 — semantic relation extractor.

Produces ``SemanticRelationCandidate`` records that satisfy the Phase 31
``semantic_relation_candidate`` artifact contract
(``packs/research_tech/artifacts.py``). Output flows into the
``60-Logs/derived/review-queue/semantic-relations/<subject>.json`` queue;
promotion to the ``relations`` table is gated by ``promotion_policy``.

The proposer is a ``Protocol`` so callers can plug in an LLM, a regex
heuristic, or test stubs. Vocabulary and object-kind constraints come from
``pack.semantic_relation_contracts()``; candidates that violate the contract
are dropped with a ``rejection_reason`` so the doctor can surface them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Protocol

from ..derived.paths import review_queue_path
from ..evidence import compute_content_hash, compute_locator, compute_retrieval_context
from ..packs.base import BaseDomainPack, SemanticRelationTypeSpec
from ..runtime import VaultLayout


@dataclass(frozen=True)
class SemanticRelationCandidate:
    """One proposed relation between two canonical objects.

    Field shape mirrors the Phase 31 ``semantic_relation_candidate``
    ``ArtifactSpec`` plus the Phase 33 evidence fields (``locator``,
    ``content_hash``, ``retrieval_context``) so the row can move into the
    ``relations`` table without a second hop.
    """

    relation_type: str
    source_object_id: str
    target_object_id: str
    source_slug: str
    evidence_quote: str
    confidence: float
    locator: str = ""
    content_hash: str = ""
    retrieval_context: str = ""
    pack: str = ""
    relation_subtype: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SemanticRelationCandidate":
        return cls(
            relation_type=str(data.get("relation_type") or ""),
            source_object_id=str(data.get("source_object_id") or ""),
            target_object_id=str(data.get("target_object_id") or ""),
            source_slug=str(data.get("source_slug") or ""),
            evidence_quote=str(data.get("evidence_quote") or ""),
            confidence=float(data.get("confidence") or 0.0),
            locator=str(data.get("locator") or ""),
            content_hash=str(data.get("content_hash") or ""),
            retrieval_context=str(data.get("retrieval_context") or ""),
            pack=str(data.get("pack") or ""),
            relation_subtype=str(data.get("relation_subtype") or ""),
        )


@dataclass
class ExtractionReport:
    candidates: list[SemanticRelationCandidate] = field(default_factory=list)
    rejected: list[tuple[SemanticRelationCandidate, str]] = field(default_factory=list)


class RelationProposer(Protocol):
    """Implementations turn deep-dive text into raw relation proposals.

    The contract is intentionally minimal: take the full source text plus the
    canonical-object id pool, return zero or more candidates. Validation,
    locator computation, and content hashing are performed by
    ``extract_relations`` so proposers can stay stateless.
    """

    def propose(
        self,
        text: str,
        *,
        source_slug: str,
        vocabulary: tuple[str, ...],
        known_object_ids: tuple[str, ...],
    ) -> Iterable[SemanticRelationCandidate]:
        ...


def _allowed_pairs(
    relation_type: SemanticRelationTypeSpec,
    object_kinds: dict[str, str],
    candidate: SemanticRelationCandidate,
) -> bool:
    """Both endpoints must satisfy the relation type's kind constraints.

    Empty kind tuples mean "any kind allowed" — matches
    ``SemanticRelationTypeSpec`` semantics.
    """
    src_kind = object_kinds.get(candidate.source_object_id, "")
    tgt_kind = object_kinds.get(candidate.target_object_id, "")
    if relation_type.source_object_kinds and src_kind not in relation_type.source_object_kinds:
        return False
    if relation_type.target_object_kinds and tgt_kind not in relation_type.target_object_kinds:
        return False
    return True


def _vocabulary(pack: BaseDomainPack) -> dict[str, SemanticRelationTypeSpec]:
    vocab: dict[str, SemanticRelationTypeSpec] = {}
    for contract in pack.semantic_relation_contracts():
        for spec in contract.relation_types:
            vocab[spec.name] = spec
    return vocab


def extract_relations(
    deep_dive_path: Path,
    *,
    pack: BaseDomainPack,
    vault_dir: Path,
    proposer: RelationProposer,
    known_object_ids: Iterable[str],
    object_kinds: dict[str, str] | None = None,
) -> ExtractionReport:
    """Drive the proposer, then validate against the pack contract.

    Validation rejects: unknown ``relation_type``, missing ``source_slug``,
    missing ``evidence_quote``, ids absent from ``known_object_ids``, and
    object-kind constraint violations. Each surviving candidate gets the
    locator/hash/context fields filled in from the source file.
    """
    text = deep_dive_path.read_text(encoding="utf-8")
    source_slug = deep_dive_path.stem
    content_hash = compute_content_hash(deep_dive_path, vault_dir=vault_dir)
    vocabulary = _vocabulary(pack)
    known = set(known_object_ids)
    object_kinds = object_kinds or {}

    report = ExtractionReport()

    evolves_subtypes = set(pack.evolves_relation_types())

    for raw in proposer.propose(
        text,
        source_slug=source_slug,
        vocabulary=tuple(vocabulary.keys()),
        known_object_ids=tuple(known),
    ):
        candidate = SemanticRelationCandidate(
            relation_type=raw.relation_type,
            source_object_id=raw.source_object_id,
            target_object_id=raw.target_object_id,
            source_slug=raw.source_slug or source_slug,
            evidence_quote=raw.evidence_quote,
            confidence=raw.confidence,
            locator=raw.locator or compute_locator(deep_dive_path, raw.evidence_quote, vault_dir=vault_dir),
            content_hash=raw.content_hash or content_hash,
            retrieval_context=raw.retrieval_context
            or compute_retrieval_context(deep_dive_path, raw.evidence_quote, vault_dir=vault_dir),
            pack=pack.name,
            relation_subtype=raw.relation_subtype,
        )

        if candidate.relation_type not in vocabulary:
            report.rejected.append((candidate, "unknown_relation_type"))
            continue
        if not candidate.evidence_quote.strip():
            report.rejected.append((candidate, "missing_evidence_quote"))
            continue
        if not candidate.source_slug.strip():
            report.rejected.append((candidate, "missing_source_slug"))
            continue
        if known and candidate.source_object_id not in known:
            report.rejected.append((candidate, "unknown_source_object_id"))
            continue
        if known and candidate.target_object_id not in known:
            report.rejected.append((candidate, "unknown_target_object_id"))
            continue
        if not _allowed_pairs(vocabulary[candidate.relation_type], object_kinds, candidate):
            report.rejected.append((candidate, "kind_constraint_violation"))
            continue
        if candidate.relation_type == "evolves":
            subtype = candidate.relation_subtype.strip()
            if not subtype:
                report.rejected.append((candidate, "missing_relation_subtype"))
                continue
            if subtype not in evolves_subtypes:
                report.rejected.append((candidate, "unknown_relation_subtype"))
                continue
        report.candidates.append(candidate)

    return report


def candidate_subject(candidate: SemanticRelationCandidate) -> str:
    """Derive the review-queue file stem for a candidate.

    Public so the promoter can locate (and delete) a candidate's queue file
    after promotion without re-globbing the directory.
    """
    return f"{candidate.source_object_id}__{candidate.relation_type}__{candidate.target_object_id}"


def write_candidates(
    candidates: Iterable[SemanticRelationCandidate],
    *,
    layout: VaultLayout,
    queue_name: str = "semantic-relations",
) -> list[Path]:
    """Serialize candidates to the spec-declared review-queue path.

    One file per ``(source, relation_type, target)`` triple. Idempotent —
    re-running with the same candidate overwrites with a fresh
    ``content_hash`` snapshot.
    """
    written: list[Path] = []
    for candidate in candidates:
        path = review_queue_path(
            layout,
            queue_name=queue_name,
            subject=candidate_subject(candidate),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(candidate.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path)
    return written


def load_candidates(
    layout: VaultLayout,
    *,
    queue_name: str = "semantic-relations",
) -> list[SemanticRelationCandidate]:
    base = layout.review_queue_dir / queue_name
    if not base.exists():
        return []
    out: list[SemanticRelationCandidate] = []
    for path in sorted(base.glob("*.json")):
        out.append(SemanticRelationCandidate.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return out
