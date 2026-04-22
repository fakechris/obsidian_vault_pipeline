"""Phase 36 — query feedback router.

Closes the Capture → Compile → Reuse loop: ``ovp-query`` becomes a *producer*
of new candidates, claims, open questions, and writing prompts. Each downstream
artifact is routed through the same shaped streams so MCP tools (Phase 37) can
expose them as typed primitives.

Streams (each a small ``TypedDict``-style dataclass — frozen for hashability):

* ``CitedClaim``           — already-canonical claim cited in the answer; emits
                             a ``trusted_reuse_event`` (existing Phase 32 path)
* ``CandidateConcept``     — unknown term that survived disambiguation; calls
                             ``ConceptRegistry.upsert_candidate`` and writes a
                             ``state: candidate`` provenance line
* ``OpenQuestion``         — question the model could not answer; appended to
                             ``60-Logs/open-questions.jsonl``
* ``WritingPrompt``        — rich prompt the model surfaced for the user; appended
                             to ``00-Polaris/Writing-Prompts.md`` (the single
                             append-only file inside an accepted zone — see
                             ``research_tech.workspace_zones``)
* ``ProposedRelation``     — semantic relation candidate; written via Phase 35
                             ``write_candidates`` so Phase 35 promotion sees it

Each successful route emits one ``feedback_yield`` event so the doctor's
"query→candidate yield" metric stays accurate. Append paths are deliberately
*outside* ``enforce_zone_write`` because the corresponding pack zone marks them
``append_only`` — the lint rule respects that.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .event_emitter import emit
from .extraction.semantic_relations import SemanticRelationCandidate, write_candidates
from .packs.base import BaseDomainPack
from .runtime import VaultLayout
from .state_lifecycle import State
from .workspace_promotion import WRITE_MODE_APPEND, enforce_zone_write


# ---------------------------------------------------------------------------
# Stream shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitedClaim:
    slug: str
    surface: str  # "query"
    consumer_ref: str


@dataclass(frozen=True)
class CandidateConcept:
    term: str
    definition: str = ""
    area: str = ""

    def to_slug(self) -> str:
        return self.term.strip().lower().replace(" ", "-").replace("/", "-")


@dataclass(frozen=True)
class OpenQuestion:
    question: str
    consumer_ref: str = ""


@dataclass(frozen=True)
class WritingPrompt:
    prompt: str
    rationale: str = ""


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


def _emit_yield(
    vault_dir: Path,
    *,
    pack: str,
    stream: str,
    payload: dict[str, object],
) -> None:
    body = {"stream": stream, **payload}
    emit(vault_dir, "pipeline.jsonl", "feedback_yield", body, pack=pack)


def route_candidate_concepts(
    concepts: Iterable[CandidateConcept],
    *,
    vault_dir: Path,
    pack: BaseDomainPack,
) -> int:
    """Add unknown terms to the concept registry as candidates.

    Returns the number of registry rows actually inserted (existing candidates
    are bumped but not double-counted).
    """
    from .concept_registry import ConceptRegistry

    registry = ConceptRegistry(vault_dir).load()
    inserted = 0
    for concept in concepts:
        existing = registry.find_by_slug(concept.to_slug())
        registry.upsert_candidate(
            slug=concept.to_slug(),
            title=concept.term,
            definition=concept.definition,
            area=concept.area,
        )
        if existing is None:
            inserted += 1
            _emit_yield(
                vault_dir,
                pack=pack.name,
                stream="candidate_concept",
                payload={
                    "slug": concept.to_slug(),
                    "term": concept.term,
                    "state": State.CANDIDATE.value,
                },
            )
    if inserted:
        registry.save()
    return inserted


def route_open_questions(
    questions: Iterable[OpenQuestion],
    *,
    vault_dir: Path,
    pack: BaseDomainPack,
) -> int:
    """Append each question to ``60-Logs/open-questions.jsonl``.

    The ``60-Logs/**`` glob is in the ``append_only`` zone whitelist for both
    packs, so this write does not require ``enforce_zone_write``.
    """
    layout = VaultLayout.from_vault(vault_dir)
    target = layout.logs_dir / "open-questions.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("a", encoding="utf-8") as handle:
        for question in questions:
            line = emit_line({"question": question.question, "consumer_ref": question.consumer_ref})
            handle.write(line)
            written += 1
            _emit_yield(
                vault_dir,
                pack=pack.name,
                stream="open_question",
                payload={"question": question.question},
            )
    return written


def route_writing_prompts(
    prompts: Iterable[WritingPrompt],
    *,
    vault_dir: Path,
    pack: BaseDomainPack,
) -> int:
    """Append prompts to ``00-Polaris/Writing-Prompts.md``.

    This is the single append-only file living inside an accepted zone; the
    pack must list it under ``workspace_zones.append_only``.
    ``enforce_zone_write`` is called with ``mode='append'`` so a misconfigured
    pack still raises ``ZoneViolation`` instead of silently corrupting an
    accepted file.
    """
    target = vault_dir / "00-Polaris" / "Writing-Prompts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("# Writing Prompts\n\n", encoding="utf-8")
    enforce_zone_write(target, pack=pack, vault_dir=vault_dir, mode=WRITE_MODE_APPEND)

    written = 0
    with target.open("a", encoding="utf-8") as handle:
        for prompt in prompts:
            handle.write(f"\n- {prompt.prompt}")
            if prompt.rationale:
                handle.write(f"\n  - _why_: {prompt.rationale}")
            handle.write("\n")
            written += 1
            _emit_yield(
                vault_dir,
                pack=pack.name,
                stream="writing_prompt",
                payload={"prompt": prompt.prompt},
            )
    return written


def route_proposed_relations(
    relations: Iterable[SemanticRelationCandidate],
    *,
    vault_dir: Path,
    pack: BaseDomainPack,
) -> list[Path]:
    """Hand semantic relation proposals to Phase 35's review queue.

    The candidates flow into ``60-Logs/derived/review-queue/semantic-relations``
    where ``ovp-promote relations`` picks them up.
    """
    layout = VaultLayout.from_vault(vault_dir)
    rels = list(relations)
    paths = write_candidates(rels, layout=layout)
    for candidate in rels:
        _emit_yield(
            vault_dir,
            pack=pack.name,
            stream="proposed_relation",
            payload={
                "relation_type": candidate.relation_type,
                "source_object_id": candidate.source_object_id,
                "target_object_id": candidate.target_object_id,
            },
        )
    return paths


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def emit_line(payload: dict[str, object]) -> str:
    """Serialize an open-questions line.

    Kept small and dependency-free so the same shape can be replayed by the
    UI panel. Newline-terminated, JSON-encoded.
    """
    import json
    from datetime import datetime, timezone

    body = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **payload}
    return json.dumps(body, ensure_ascii=False) + "\n"
