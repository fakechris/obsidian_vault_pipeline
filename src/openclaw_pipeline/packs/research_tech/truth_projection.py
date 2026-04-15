from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
from pathlib import Path

from ...truth_store import TruthStoreProjection

_NEGATION_RE = re.compile(
    r"\b(?:does not|doesn't|do not|is not|isn't|are not|aren't|has not|hasn't|have not|haven't|cannot|can't|not)\b"
)
_NEGATION_EXCLUSIONS = (
    "not only",
    "not necessarily",
    "not merely",
    "not just",
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


def _subject_key(claim_text: str) -> str:
    lowered = claim_text.strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    for marker in (" supports ", " does not support ", " is ", " are ", " has ", " have "):
        if marker in lowered:
            return lowered.split(marker, 1)[0].strip()
    return lowered.split(".", 1)[0].strip()


def _contradiction_id_for_subject(subject: str) -> str:
    return f"contradiction::{hashlib.sha1(subject.encode('utf-8')).hexdigest()[:12]}"


def _is_negative_claim(claim_text: str) -> bool:
    lowered = claim_text.strip().lower()
    if any(exclusion in lowered for exclusion in _NEGATION_EXCLUSIONS):
        return False
    return bool(_NEGATION_RE.search(lowered))


def _detect_contradictions(
    pack_name: str,
    claims: list[tuple[str, str, str, str, str, float]],
) -> list[tuple[str, str, str, str, str, str, str, str]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for _pack, claim_id, _object_id, claim_kind, claim_text, _confidence in claims:
        if claim_kind != "page_summary":
            continue
        subject = _subject_key(claim_text)
        if not subject:
            continue
        grouped.setdefault(subject, []).append((claim_id, claim_text))

    contradictions: list[tuple[str, str, str, str, str, str, str, str]] = []
    for subject, rows in grouped.items():
        positives = [claim_id for claim_id, text in rows if not _is_negative_claim(text)]
        negatives = [claim_id for claim_id, text in rows if _is_negative_claim(text)]
        if not positives or not negatives:
            continue
        contradiction_id = _contradiction_id_for_subject(subject)
        contradictions.append(
            (
                pack_name,
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


def _graph_edge_id(source_object_id: str, target_object_id: str, edge_kind: str) -> str:
    fingerprint = f"{source_object_id}:{target_object_id}:{edge_kind}"
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def _graph_cluster_id(member_object_ids: list[str]) -> str:
    fingerprint = "::".join(member_object_ids)
    return f"cluster::{hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()[:12]}"


def _build_graph_seeds(
    pack_name: str,
    *,
    objects: list[tuple[str, str, str, str, str, str]],
    relations: list[tuple[str, str, str, str, str]],
    contradictions: list[tuple[str, str, str, str, str, str, str, str]],
) -> tuple[
    list[tuple[str, str, str, str, str, float, str]],
    list[tuple[str, str, str, str, str, str, float]],
]:
    edge_rows: dict[str, tuple[str, str, str, str, str, float, str]] = {}
    adjacency: dict[str, set[str]] = defaultdict(set)
    object_titles = {object_id: title for _pack, object_id, _kind, title, _path, _source in objects}

    for _pack, source_object_id, target_object_id, relation_type, evidence_source_slug in relations:
        edge_kind = f"relation:{relation_type}"
        edge_id = _graph_edge_id(source_object_id, target_object_id, edge_kind)
        edge_rows[edge_id] = (
            pack_name,
            edge_id,
            source_object_id,
            target_object_id,
            edge_kind,
            1.0,
            evidence_source_slug,
        )
        adjacency[source_object_id].add(target_object_id)
        adjacency[target_object_id].add(source_object_id)

    for _pack, _contradiction_id, _subject_key, positive_json, negative_json, _status, _note, _resolved_at in contradictions:
        positive_ids = {claim_id.split("::", 1)[0] for claim_id in json.loads(positive_json)}
        negative_ids = {claim_id.split("::", 1)[0] for claim_id in json.loads(negative_json)}
        for source_object_id in sorted(positive_ids):
            for target_object_id in sorted(negative_ids):
                if source_object_id == target_object_id:
                    continue
                ordered = tuple(sorted([source_object_id, target_object_id]))
                edge_kind = "contradiction:subject"
                edge_id = _graph_edge_id(ordered[0], ordered[1], edge_kind)
                edge_rows[edge_id] = (
                    pack_name,
                    edge_id,
                    ordered[0],
                    ordered[1],
                    edge_kind,
                    0.8,
                    "",
                )
                adjacency[ordered[0]].add(ordered[1])
                adjacency[ordered[1]].add(ordered[0])

    visited: set[str] = set()
    cluster_rows: list[tuple[str, str, str, str, str, str, float]] = []
    for object_id in sorted(object_titles):
        if object_id in visited:
            continue
        stack = [object_id]
        component: list[str] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            stack.extend(sorted(adjacency.get(current, set()) - visited))
        component = sorted(component)
        if len(component) < 2:
            continue
        center_object_id = max(
            component,
            key=lambda candidate: (
                len(adjacency.get(candidate, set())),
                object_titles.get(candidate, ""),
                candidate,
            ),
        )
        cluster_rows.append(
            (
                pack_name,
                _graph_cluster_id(component),
                "relation_component",
                object_titles.get(center_object_id, center_object_id),
                center_object_id,
                json.dumps(component, ensure_ascii=False),
                float(len(component)),
            )
        )

    return list(edge_rows.values()), cluster_rows


def build_truth_projection(
    *,
    vault_dir: Path,
    page_rows: list[tuple[str, str, str, str, str, str, str]],
    link_rows: list[tuple[str, str, str, str, int]],
    pack_name: str | None = None,
    spec: object | None = None,
) -> TruthStoreProjection:
    _ = vault_dir, spec
    resolved_pack_name = str(pack_name or "research-tech")

    objects: list[tuple[str, str, str, str, str, str]] = []
    claims: list[tuple[str, str, str, str, str, float]] = []
    claim_evidence: list[tuple[str, str, str, str, str]] = []
    compiled_summaries: list[tuple[str, str, str, str]] = []

    for slug, title, note_type, path, _day_id, _frontmatter_json, body in page_rows:
        objects.append((resolved_pack_name, slug, note_type, title, path, slug))
        summary = _page_summary(body, fallback=title)
        claim_id = _claim_id(slug, summary)
        claims.append((resolved_pack_name, claim_id, slug, "page_summary", summary, 1.0))
        claim_evidence.append((resolved_pack_name, claim_id, slug, "body_summary", summary))
        compiled_summaries.append((resolved_pack_name, slug, summary, slug))

    relations = [
        (resolved_pack_name, source_slug, target_slug, relation_type, source_slug)
        for source_slug, target_slug, _target_raw, relation_type, _line_number in link_rows
    ]
    contradictions = _detect_contradictions(resolved_pack_name, claims)
    graph_edges, graph_clusters = _build_graph_seeds(
        resolved_pack_name,
        objects=objects,
        relations=relations,
        contradictions=contradictions,
    )

    return TruthStoreProjection(
        objects=objects,
        claims=claims,
        claim_evidence=claim_evidence,
        relations=relations,
        compiled_summaries=compiled_summaries,
        contradictions=contradictions,
        graph_edges=graph_edges,
        graph_clusters=graph_clusters,
    )
