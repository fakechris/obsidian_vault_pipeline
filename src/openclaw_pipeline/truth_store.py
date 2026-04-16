from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re


TRUTH_STORE_SCHEMA = """
CREATE TABLE objects (
  pack TEXT NOT NULL,
  object_id TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  title TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  source_slug TEXT NOT NULL,
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
  quote_text TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_claim_evidence_pack_claim ON claim_evidence(pack, claim_id);

CREATE TABLE relations (
  pack TEXT NOT NULL,
  source_object_id TEXT NOT NULL,
  target_object_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  evidence_source_slug TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_relations_pack_source ON relations(pack, source_object_id);
CREATE INDEX idx_relations_pack_target ON relations(pack, target_object_id);

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
class TruthStoreProjection:
    objects: list[tuple[str, str, str, str, str, str]] = field(default_factory=list)
    claims: list[tuple[str, str, str, str, str, float]] = field(default_factory=list)
    claim_evidence: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    relations: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    compiled_summaries: list[tuple[str, str, str, str]] = field(default_factory=list)
    contradictions: list[tuple[str, str, str, str, str, str, str, str]] = field(default_factory=list)
    graph_edges: list[tuple[str, str, str, str, str, float, str]] = field(default_factory=list)
    graph_clusters: list[tuple[str, str, str, str, str, str, float]] = field(default_factory=list)
