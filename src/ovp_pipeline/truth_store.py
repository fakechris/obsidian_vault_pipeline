from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Iterable


EVIDENCE_STATUS_UNVERIFIED = "unverified"
EVIDENCE_STATUS_VERIFIED = "verified"
EVIDENCE_STATUS_STALE = "stale"
EVIDENCE_STATUS_BROKEN = "broken"

EVIDENCE_STATUS_VALUES = frozenset(
    {
        EVIDENCE_STATUS_UNVERIFIED,
        EVIDENCE_STATUS_VERIFIED,
        EVIDENCE_STATUS_STALE,
        EVIDENCE_STATUS_BROKEN,
    }
)


TRUTH_STORE_SCHEMA = """
CREATE TABLE objects (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  title TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  -- BL-054: URL of the source article that produced this object.
  -- Populated from evergreen frontmatter ``source_url`` during
  -- ``rebuild_knowledge_index``.  Empty for legacy rows that have
  -- not yet been backfilled — those are scored as ``unknown source``
  -- by the source-diversity signal, not as a unique source.
  source_url TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, object_id)
);

CREATE TABLE claims (
  pack TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  claim_kind TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (pack, claim_id)
);

CREATE INDEX idx_claims_pack_object ON claims(pack, object_id);

CREATE TABLE claim_evidence (
  pack TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  evidence_kind TEXT NOT NULL,
  quote_text TEXT NOT NULL DEFAULT '',
  locator TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  retrieval_context TEXT NOT NULL DEFAULT '',
  quote_start_line INTEGER NOT NULL DEFAULT 0,
  quote_end_line INTEGER NOT NULL DEFAULT 0,
  quote_start_char INTEGER NOT NULL DEFAULT 0,
  quote_end_char INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'unverified',
  verified_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_claim_evidence_pack_claim ON claim_evidence(pack, claim_id);
CREATE INDEX idx_claim_evidence_status ON claim_evidence(pack, status);

CREATE TABLE relations (
  pack TEXT NOT NULL,
  source_object_id TEXT NOT NULL,
  target_object_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  evidence_source_slug TEXT NOT NULL DEFAULT '',
  quote_text TEXT NOT NULL DEFAULT '',
  locator TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  retrieval_context TEXT NOT NULL DEFAULT '',
  quote_start_line INTEGER NOT NULL DEFAULT 0,
  quote_end_line INTEGER NOT NULL DEFAULT 0,
  quote_start_char INTEGER NOT NULL DEFAULT 0,
  quote_end_char INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'unverified',
  verified_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_relations_pack_source ON relations(pack, source_object_id);
CREATE INDEX idx_relations_pack_target ON relations(pack, target_object_id);
CREATE INDEX idx_relations_status ON relations(pack, status);

CREATE TABLE compiled_summaries (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  PRIMARY KEY (pack, object_id)
);

CREATE TABLE contradictions (
  pack TEXT NOT NULL,
  contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution_note TEXT NOT NULL DEFAULT '',
  resolved_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id)
);

CREATE INDEX idx_contradictions_pack_subject ON contradictions(pack, subject_key);

CREATE TABLE graph_edges (
  pack TEXT NOT NULL,
  edge_id TEXT NOT NULL,
  source_object_id TEXT NOT NULL,
  target_object_id TEXT NOT NULL,
  edge_kind TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  evidence_source_slug TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, edge_id)
);

CREATE INDEX idx_graph_edges_pack_source ON graph_edges(pack, source_object_id);
CREATE INDEX idx_graph_edges_pack_target ON graph_edges(pack, target_object_id);

CREATE TABLE graph_clusters (
  pack TEXT NOT NULL,
  cluster_id TEXT NOT NULL,
  cluster_kind TEXT NOT NULL,
  label TEXT NOT NULL,
  center_object_id TEXT NOT NULL,
  member_object_ids_json TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY (pack, cluster_id)
);

CREATE INDEX idx_graph_clusters_pack_kind ON graph_clusters(pack, cluster_kind);

CREATE TABLE truth_projections (
  pack TEXT PRIMARY KEY,
  owner_pack TEXT NOT NULL,
  builder_name TEXT NOT NULL DEFAULT '',
  built_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE reuse_events (
  event_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL DEFAULT '',
  object_kind TEXT NOT NULL DEFAULT '',
  surface TEXT NOT NULL,
  consumer_ref TEXT NOT NULL DEFAULT '',
  evidence_present INTEGER NOT NULL DEFAULT 0,
  provenance_clean INTEGER NOT NULL DEFAULT 0,
  trusted INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_reuse_events_pack_surface ON reuse_events(pack, surface);
CREATE INDEX idx_reuse_events_object       ON reuse_events(pack, object_id);
CREATE INDEX idx_reuse_events_ts           ON reuse_events(ts);

CREATE TABLE community_crystals (
  pack TEXT NOT NULL,
  cluster_id TEXT NOT NULL,
  body_md TEXT NOT NULL,
  source_evergreen_slugs_json TEXT NOT NULL,
  synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, cluster_id, synthesized_at)
);

CREATE INDEX idx_community_crystals_pack_cluster
  ON community_crystals(pack, cluster_id);

CREATE TABLE contradiction_crystals (
  pack TEXT NOT NULL,
  contradiction_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  body_md TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  source_object_ids_json TEXT NOT NULL,
  synthesized_at TEXT NOT NULL,
  llm_model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  superseded_by_synthesized_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, contradiction_id, synthesized_at)
);

CREATE INDEX idx_contradiction_crystals_pack_id
  ON contradiction_crystals(pack, contradiction_id);

-- M14 BL-045: per-crystal score, derived from existing Projections
-- + Canonical-State signals.  Drives the curated Atlas top-N
-- ranking (BL-046).  Re-derived on every ``ovp-knowledge-index``
-- run; ``crystal_scores`` is itself a Projection (deletable +
-- rebuildable, never authoritative).
CREATE TABLE crystal_scores (
  pack TEXT NOT NULL,
  crystal_kind TEXT NOT NULL,
  crystal_id TEXT NOT NULL,
  score REAL NOT NULL,
  size_norm REAL NOT NULL DEFAULT 0,
  credibility_norm REAL NOT NULL DEFAULT 0,
  contradiction_norm REAL NOT NULL DEFAULT 0,
  reuse_recency_norm REAL NOT NULL DEFAULT 0,
  evergreen_recency_norm REAL NOT NULL DEFAULT 0,
  -- BL-054: unique-source coverage of the community.  Penalises
  -- topics where many evergreens came from one source article.
  source_diversity_norm REAL NOT NULL DEFAULT 0,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (pack, crystal_kind, crystal_id)
);

CREATE INDEX idx_crystal_scores_pack_score
  ON crystal_scores(pack, score DESC);

-- BL-055: provenance spine.  Every Canonical-State object that the
-- system creates writes (or has populated for it) at least one row
-- here.  Append-only on PK ``(pack, object_id, stage, derived_at)``.
-- Read pattern for the hot path (scoring) stays denormalised on
-- ``objects.source_url``; this table is the audit + multi-stage
-- source of truth.  See docs/plans/2026-05-04-bl-055-provenance-spine.md.
CREATE TABLE provenance (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  source_fingerprint TEXT NOT NULL DEFAULT '',
  derived_via_stage TEXT NOT NULL,
  derived_at TEXT NOT NULL,
  parent_object_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (pack, object_id, derived_via_stage, derived_at)
);

CREATE INDEX idx_provenance_object
  ON provenance(pack, object_id);
CREATE INDEX idx_provenance_source
  ON provenance(pack, source_url);
CREATE INDEX idx_provenance_stage
  ON provenance(pack, derived_via_stage);

-- BL-061: prose-level revision history for evergreens.  Where
-- ``provenance`` answers "what stage produced this object?",
-- ``evergreen_revisions`` answers "what did this object's content
-- look like before the last LLM rewrite?".  Append-only;
-- monotonically increasing ``version`` per ``(pack, object_id)``;
-- writers populate it at every mutation site (extract / promote /
-- editor_edit / llm_rewrite / rollback).
--
-- Owner: ``truth_store_writers.record_evergreen_revision`` (BL-060).
-- Read patterns: ``/object?id=…&tab=history`` (latest N) +
-- ``ovp-rollback-evergreen <slug> <version>`` (single row).
CREATE TABLE evergreen_revisions (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  content_md TEXT NOT NULL,
  change_type TEXT NOT NULL,
  changed_by TEXT NOT NULL DEFAULT '',
  derived_at TEXT NOT NULL,
  change_note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (pack, object_id, version)
);

CREATE INDEX idx_evergreen_revisions_object
  ON evergreen_revisions(pack, object_id);
CREATE INDEX idx_evergreen_revisions_changed_at
  ON evergreen_revisions(derived_at);
"""

CONTRADICTION_HEURISTIC_NOTE = (
    "TODO: current contradiction rows are pack-owned heuristics, not core truth. "
    "If contradiction quality is noisy, tighten the active pack's subject normalization, "
    "claim grouping, and review semantics instead of teaching core one more domain rule."
)

_NEGATION_RE = re.compile(
    r"\b(?:does not|doesn't|do not|is not|isn't|are not|aren't|has not|hasn't|have not|haven't|cannot|can't|not)\b"
)
_NEGATION_EXCLUSIONS = (
    "not only",
    "not necessarily",
    "not merely",
    "not just",
)


def subject_key(claim_text: str) -> str:
    lowered = claim_text.strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    for marker in (" supports ", " does not support ", " is ", " are ", " has ", " have "):
        if marker in lowered:
            return lowered.split(marker, 1)[0].strip()
    return lowered.split(".", 1)[0].strip()


def _is_negative_claim(claim_text: str) -> bool:
    lowered = claim_text.strip().lower()
    if any(exclusion in lowered for exclusion in _NEGATION_EXCLUSIONS):
        return False
    return bool(_NEGATION_RE.search(lowered))


def _detect_contradictions(
    claims: list[tuple[str, str, str, str, float]],
) -> list[tuple[str, str, str, str, str, str, str]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for claim_id, _object_id, claim_kind, claim_text, _confidence in claims:
        if claim_kind != "page_summary":
            continue
        subject = subject_key(claim_text)
        if not subject:
            continue
        grouped.setdefault(subject, []).append((claim_id, claim_text))

    contradictions: list[tuple[str, str, str, str, str, str, str]] = []
    for subject, rows in grouped.items():
        positives = [claim_id for claim_id, text in rows if not _is_negative_claim(text)]
        negatives = [claim_id for claim_id, text in rows if _is_negative_claim(text)]
        if not positives or not negatives:
            continue
        fingerprint = re.sub(r"\s+", " ", subject.strip().lower())
        contradiction_id = f"contradiction::{hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()[:12]}"
        contradictions.append(
            (
                contradiction_id,
                subject,
                json.dumps(positives, ensure_ascii=False),
                json.dumps(negatives, ensure_ascii=False),
                "open",
                "",
                "",
            )
        )
    return contradictions


@dataclass(frozen=True)
class ObjectRow:
    pack: str
    object_id: str
    object_kind: str
    title: str
    canonical_path: str
    source_slug: str
    # BL-054: URL of the source article that produced this object.
    # Populated from frontmatter ``source_url``; defaults to "" so
    # legacy callers and pre-backfill rows still construct cleanly.
    source_url: str = ""

    def to_row(self) -> tuple[str, str, str, str, str, str, str]:
        from .object_kinds import normalize_kind

        return (
            self.pack,
            self.object_id,
            normalize_kind(self.object_kind),
            self.title,
            self.canonical_path,
            self.source_slug,
            self.source_url,
        )


@dataclass(frozen=True)
class ClaimRow:
    pack: str
    claim_id: str
    object_id: str
    claim_kind: str
    claim_text: str
    confidence: float = 1.0

    def to_row(self) -> tuple[str, str, str, str, str, float]:
        return (
            self.pack,
            self.claim_id,
            self.object_id,
            self.claim_kind,
            self.claim_text,
            float(self.confidence),
        )


@dataclass(frozen=True)
class ClaimEvidenceRow:
    """One ``claim_evidence`` row.

    Phase 33 widened this to include locator/content_hash/retrieval_context for
    re-locatability and status/verified_at for the verifier loop. Old 5-field
    callers may pass positional args; new fields default to '' / 'unverified'.
    """

    pack: str
    claim_id: str
    source_slug: str
    evidence_kind: str
    quote_text: str = ""
    locator: str = ""
    content_hash: str = ""
    retrieval_context: str = ""
    quote_start_line: int = 0
    quote_end_line: int = 0
    quote_start_char: int = 0
    quote_end_char: int = 0
    status: str = EVIDENCE_STATUS_UNVERIFIED
    verified_at: str = ""

    def to_row(self) -> tuple[str, str, str, str, str, str, str, str, int, int, int, int, str, str]:
        return (
            self.pack,
            self.claim_id,
            self.source_slug,
            self.evidence_kind,
            self.quote_text,
            self.locator,
            self.content_hash,
            self.retrieval_context,
            int(self.quote_start_line),
            int(self.quote_end_line),
            int(self.quote_start_char),
            int(self.quote_end_char),
            self.status,
            self.verified_at,
        )


@dataclass(frozen=True)
class RelationRow:
    """One ``relations`` row.

    Phase 33 added the same evidence columns as ``ClaimEvidenceRow`` so Phase 35
    can promote semantic relations with re-locatable evidence without a second
    migration.
    """

    pack: str
    source_object_id: str
    target_object_id: str
    relation_type: str
    evidence_source_slug: str = ""
    quote_text: str = ""
    locator: str = ""
    content_hash: str = ""
    retrieval_context: str = ""
    quote_start_line: int = 0
    quote_end_line: int = 0
    quote_start_char: int = 0
    quote_end_char: int = 0
    status: str = EVIDENCE_STATUS_UNVERIFIED
    verified_at: str = ""

    def to_row(self) -> tuple[str, str, str, str, str, str, str, str, int, int, int, int, str, str, str]:
        return (
            self.pack,
            self.source_object_id,
            self.target_object_id,
            self.relation_type,
            self.evidence_source_slug,
            self.quote_text,
            self.locator,
            self.content_hash,
            self.retrieval_context,
            int(self.quote_start_line),
            int(self.quote_end_line),
            int(self.quote_start_char),
            int(self.quote_end_char),
            self.status,
            self.verified_at,
        )


@dataclass(frozen=True)
class CompiledSummaryRow:
    pack: str
    object_id: str
    summary_text: str
    source_slug: str

    def to_row(self) -> tuple[str, str, str, str]:
        return (self.pack, self.object_id, self.summary_text, self.source_slug)


@dataclass(frozen=True)
class ContradictionRow:
    pack: str
    contradiction_id: str
    subject_key: str
    positive_claim_ids_json: str
    negative_claim_ids_json: str
    status: str = "open"
    resolution_note: str = ""
    resolved_at: str = ""

    def to_row(self) -> tuple[str, str, str, str, str, str, str, str]:
        return (
            self.pack,
            self.contradiction_id,
            self.subject_key,
            self.positive_claim_ids_json,
            self.negative_claim_ids_json,
            self.status,
            self.resolution_note,
            self.resolved_at,
        )


@dataclass(frozen=True)
class GraphEdgeRow:
    pack: str
    edge_id: str
    source_object_id: str
    target_object_id: str
    edge_kind: str
    weight: float = 1.0
    evidence_source_slug: str = ""

    def to_row(self) -> tuple[str, str, str, str, str, float, str]:
        return (
            self.pack,
            self.edge_id,
            self.source_object_id,
            self.target_object_id,
            self.edge_kind,
            float(self.weight),
            self.evidence_source_slug,
        )


@dataclass(frozen=True)
class GraphClusterRow:
    pack: str
    cluster_id: str
    cluster_kind: str
    label: str
    center_object_id: str
    member_object_ids_json: str
    score: float = 0.0

    def to_row(self) -> tuple[str, str, str, str, str, str, float]:
        return (
            self.pack,
            self.cluster_id,
            self.cluster_kind,
            self.label,
            self.center_object_id,
            self.member_object_ids_json,
            float(self.score),
        )


_ROW_DATACLASSES: dict[str, type] = {
    "objects": ObjectRow,
    "claims": ClaimRow,
    "claim_evidence": ClaimEvidenceRow,
    "relations": RelationRow,
    "compiled_summaries": CompiledSummaryRow,
    "contradictions": ContradictionRow,
    "graph_edges": GraphEdgeRow,
    "graph_clusters": GraphClusterRow,
}


def _coerce_rows(values: Iterable[Any], row_type: type) -> list[Any]:
    coerced: list[Any] = []
    for item in values:
        if isinstance(item, row_type):
            coerced.append(item)
        elif isinstance(item, tuple):
            if row_type is ClaimEvidenceRow and len(item) == 10:
                coerced.append(row_type(*item[:8], 0, 0, 0, 0, *item[8:]))
                continue
            if row_type is RelationRow and len(item) == 11:
                coerced.append(row_type(*item[:9], 0, 0, 0, 0, *item[9:]))
                continue
            coerced.append(row_type(*item))
        elif isinstance(item, dict):
            coerced.append(row_type(**item))
        else:
            coerced.append(item)
    return coerced


@dataclass(frozen=True)
class TruthStoreProjection:
    """Per-row dataclass projection (Phase 33).

    Fields accept either dataclass instances OR positional tuples — tuples are
    coerced in ``__post_init__``. ``to_row()`` on each row returns the SQL
    insert tuple. Callers should construct dataclass instances directly; tuple
    input is preserved for the small number of legacy tests that build empty
    projections by name.
    """

    objects: list[ObjectRow] = field(default_factory=list)
    claims: list[ClaimRow] = field(default_factory=list)
    claim_evidence: list[ClaimEvidenceRow] = field(default_factory=list)
    relations: list[RelationRow] = field(default_factory=list)
    compiled_summaries: list[CompiledSummaryRow] = field(default_factory=list)
    contradictions: list[ContradictionRow] = field(default_factory=list)
    graph_edges: list[GraphEdgeRow] = field(default_factory=list)
    graph_clusters: list[GraphClusterRow] = field(default_factory=list)

    def __post_init__(self) -> None:
        for field_name, row_type in _ROW_DATACLASSES.items():
            current = getattr(self, field_name)
            object.__setattr__(self, field_name, _coerce_rows(current, row_type))
