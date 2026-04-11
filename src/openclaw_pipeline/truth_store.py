from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass


TRUTH_STORE_SCHEMA = """
CREATE TABLE objects (
  object_id TEXT PRIMARY KEY,
  object_kind TEXT NOT NULL,
  title TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  source_slug TEXT NOT NULL
);

CREATE TABLE claims (
  claim_id TEXT PRIMARY KEY,
  object_id TEXT NOT NULL,
  claim_kind TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0
);

CREATE INDEX idx_claims_object ON claims(object_id);

CREATE TABLE claim_evidence (
  claim_id TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  evidence_kind TEXT NOT NULL,
  quote_text TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_claim_evidence_claim ON claim_evidence(claim_id);

CREATE TABLE relations (
  source_object_id TEXT NOT NULL,
  target_object_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  evidence_source_slug TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_relations_source ON relations(source_object_id);
CREATE INDEX idx_relations_target ON relations(target_object_id);

CREATE TABLE compiled_summaries (
  object_id TEXT PRIMARY KEY,
  summary_text TEXT NOT NULL,
  source_slug TEXT NOT NULL
);

CREATE TABLE contradictions (
  contradiction_id TEXT PRIMARY KEY,
  subject_key TEXT NOT NULL,
  positive_claim_ids_json TEXT NOT NULL,
  negative_claim_ids_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution_note TEXT NOT NULL DEFAULT '',
  resolved_at TEXT NOT NULL DEFAULT ''
);
"""

_NEGATION_RE = re.compile(r"\b(?:does not|doesn't|do not|is not|isn't|are not|aren't|has not|hasn't|have not|haven't|cannot|can't|not)\b")
_NEGATION_EXCLUSIONS = (
    "not only",
    "not necessarily",
    "not merely",
    "not just",
)
CONTRADICTION_HEURISTIC_NOTE = (
    "TODO: _detect_contradictions uses a regex-based negation heuristic with exclusions. "
    "If it still produces noisy rows, tighten subject_key() normalization and contradiction_id_for_subject() grouping."
)


@dataclass(frozen=True)
class TruthStoreProjection:
    objects: list[tuple[str, str, str, str, str]]
    claims: list[tuple[str, str, str, str, float]]
    claim_evidence: list[tuple[str, str, str, str]]
    relations: list[tuple[str, str, str, str]]
    compiled_summaries: list[tuple[str, str, str]]
    contradictions: list[tuple[str, str, str, str, str, str, str]]


def build_truth_store_projection(
    page_rows: list[tuple[str, str, str, str, str, str, str]],
    link_rows: list[tuple[str, str, str, str, int]],
) -> TruthStoreProjection:
    objects: list[tuple[str, str, str, str, str]] = []
    claims: list[tuple[str, str, str, str, float]] = []
    claim_evidence: list[tuple[str, str, str, str]] = []
    compiled_summaries: list[tuple[str, str, str]] = []

    for slug, title, note_type, path, _day_id, _frontmatter_json, body in page_rows:
        objects.append((slug, note_type, title, path, slug))
        summary = _page_summary(body, fallback=title)
        claim_id = _claim_id(slug, summary)
        claims.append((claim_id, slug, "page_summary", summary, 1.0))
        claim_evidence.append((claim_id, slug, "body_summary", summary))
        compiled_summaries.append((slug, summary, slug))

    relations = [
        (source_slug, target_slug, relation_type, source_slug)
        for source_slug, target_slug, _target_raw, relation_type, _line_number in link_rows
    ]
    contradictions = _detect_contradictions(claims)

    return TruthStoreProjection(
        objects=objects,
        claims=claims,
        claim_evidence=claim_evidence,
        relations=relations,
        compiled_summaries=compiled_summaries,
        contradictions=contradictions,
    )


def _claim_id(object_id: str, claim_text: str) -> str:
    digest = hashlib.sha1(f"{object_id}:{claim_text}".encode("utf-8")).hexdigest()[:12]
    return f"{object_id}::{digest}"


def _page_summary(body: str, *, fallback: str) -> str:
    content = body.strip()
    if not content:
        return fallback
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    text = " ".join(lines)
    if not text:
        return fallback
    for marker in (". ", "! ", "? ", "。", "！", "？"):
        if marker in text:
            return text.split(marker, 1)[0].strip() + (marker.strip() if marker.strip() in "。！？" else ".")
    return text[:220]


def subject_key(claim_text: str) -> str:
    lowered = claim_text.strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    for marker in (" supports ", " does not support ", " is ", " are ", " has ", " have "):
        if marker in lowered:
            return lowered.split(marker, 1)[0].strip()
    return lowered.split(".", 1)[0].strip()


def contradiction_id_for_subject(subject: str) -> str:
    return f"contradiction::{hashlib.sha1(subject.encode('utf-8')).hexdigest()[:12]}"


def _detect_contradictions(
    claims: list[tuple[str, str, str, str, float]]
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
        contradiction_id = contradiction_id_for_subject(subject)
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


def _is_negative_claim(claim_text: str) -> bool:
    lowered = claim_text.strip().lower()
    if any(exclusion in lowered for exclusion in _NEGATION_EXCLUSIONS):
        return False
    return bool(_NEGATION_RE.search(lowered))
