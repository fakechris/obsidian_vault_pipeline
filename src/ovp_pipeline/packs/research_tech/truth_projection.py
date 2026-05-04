from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import networkx as nx
from networkx.algorithms.community import louvain_communities

from ...truth_store import (
    ClaimEvidenceRow,
    ClaimRow,
    CompiledSummaryRow,
    ContradictionRow,
    GraphClusterRow,
    GraphEdgeRow,
    ObjectRow,
    RelationRow,
    TruthStoreProjection,
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


def _contradiction_id_for_subject(pack_name: str, subject: str) -> str:
    fingerprint = f"{pack_name}:{subject}"
    return f"contradiction::{hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()[:12]}"


def _is_negative_claim(claim_text: str) -> bool:
    lowered = claim_text.strip().lower()
    if any(exclusion in lowered for exclusion in _NEGATION_EXCLUSIONS):
        return False
    return bool(_NEGATION_RE.search(lowered))


def _detect_contradictions(
    pack_name: str,
    claims: list[ClaimRow],
) -> list[ContradictionRow]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for claim in claims:
        if claim.claim_kind != "page_summary":
            continue
        subject = _subject_key(claim.claim_text)
        if not subject:
            continue
        grouped.setdefault(subject, []).append((claim.claim_id, claim.claim_text))

    contradictions: list[ContradictionRow] = []
    for subject, rows in grouped.items():
        positives = [claim_id for claim_id, text in rows if not _is_negative_claim(text)]
        negatives = [claim_id for claim_id, text in rows if _is_negative_claim(text)]
        if not positives or not negatives:
            continue
        contradiction_id = _contradiction_id_for_subject(pack_name, subject)
        contradictions.append(
            ContradictionRow(
                pack=pack_name,
                contradiction_id=contradiction_id,
                subject_key=subject,
                positive_claim_ids_json=json.dumps(positives, ensure_ascii=False),
                negative_claim_ids_json=json.dumps(negatives, ensure_ascii=False),
                status="open",
            )
        )
    return contradictions


def _graph_edge_id(source_object_id: str, target_object_id: str, edge_kind: str) -> str:
    fingerprint = f"{source_object_id}:{target_object_id}:{edge_kind}"
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def _graph_cluster_id(member_object_ids: list[str]) -> str:
    fingerprint = "::".join(member_object_ids)
    return f"cluster::{hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()[:12]}"


# Seed for Louvain so the same input graph yields the same partition
# across runs.  The algorithm is order-sensitive without a seed; with
# one fixed it becomes a pure function of the edge set, which keeps
# graph_cluster_id stable across rebuilds.
_LOUVAIN_SEED = 0


def _detect_communities(
    edge_rows: dict[str, GraphEdgeRow],
    object_ids: list[str],
) -> list[list[str]]:
    """Run Louvain community detection on the relation+contradiction graph.

    Pre-fix this was a connected-component BFS, which surfaced one
    cluster per disconnected island regardless of internal density.
    Louvain maximises modularity, so a tightly-knit subgroup inside a
    single connected component becomes its own community — which is
    what users actually mean by "knowledge-base structure".

    Returns sorted member lists for each community of size ≥ 2;
    isolated nodes and 1-member communities are dropped.
    """
    if not edge_rows:
        # Louvain's modularity calculation divides by ``deg_sum²`` and
        # raises ``ZeroDivisionError`` on an edgeless graph.  No edges
        # means no communities of size ≥ 2 anyway, so short-circuit.
        return []
    # Aggregate by unordered pair before adding to the graph.  A
    # plain ``nx.Graph.add_edge`` is last-write-wins on the weight
    # attribute, so a (relation, weight=1.0) edge and a
    # (contradiction, weight=0.8) edge between the same pair would
    # collapse to whichever was added last — usually down-weighting
    # the stronger relation.  Sum captures the right intuition: two
    # different kinds of evidence for the same connection should
    # increase its modularity contribution, not erase one of them.
    pair_weights: dict[tuple[str, str], float] = {}
    for edge in edge_rows.values():
        pair = tuple(sorted(  # type: ignore[assignment]
            (edge.source_object_id, edge.target_object_id),
        ))
        pair_weights[pair] = pair_weights.get(pair, 0.0) + edge.weight

    graph: nx.Graph = nx.Graph()
    graph.add_nodes_from(object_ids)
    for (src, tgt), weight in pair_weights.items():
        graph.add_edge(src, tgt, weight=weight)
    communities = louvain_communities(
        graph, weight="weight", seed=_LOUVAIN_SEED,
    )
    out: list[list[str]] = []
    for community in communities:
        if len(community) < 2:
            continue
        out.append(sorted(community))
    # Stable order so downstream cluster_id sequences don't drift
    # when Louvain returns equivalent partitions in different orders.
    out.sort(key=lambda members: members[0])
    return out


def _build_graph_seeds(
    pack_name: str,
    *,
    objects: list[ObjectRow],
    relations: list[RelationRow],
    contradictions: list[ContradictionRow],
) -> tuple[list[GraphEdgeRow], list[GraphClusterRow]]:
    edge_rows: dict[str, GraphEdgeRow] = {}
    adjacency: dict[str, set[str]] = defaultdict(set)
    object_titles = {row.object_id: row.title for row in objects}

    for relation in relations:
        edge_kind = f"relation:{relation.relation_type}"
        edge_id = _graph_edge_id(relation.source_object_id, relation.target_object_id, edge_kind)
        edge_rows[edge_id] = GraphEdgeRow(
            pack=pack_name,
            edge_id=edge_id,
            source_object_id=relation.source_object_id,
            target_object_id=relation.target_object_id,
            edge_kind=edge_kind,
            weight=1.0,
            evidence_source_slug=relation.evidence_source_slug,
        )
        adjacency[relation.source_object_id].add(relation.target_object_id)
        adjacency[relation.target_object_id].add(relation.source_object_id)

    for contradiction in contradictions:
        positive_ids = {
            claim_id.split("::", 1)[0]
            for claim_id in json.loads(contradiction.positive_claim_ids_json)
        }
        negative_ids = {
            claim_id.split("::", 1)[0]
            for claim_id in json.loads(contradiction.negative_claim_ids_json)
        }
        for source_object_id in sorted(positive_ids):
            for target_object_id in sorted(negative_ids):
                if source_object_id == target_object_id:
                    continue
                ordered = tuple(sorted([source_object_id, target_object_id]))
                edge_kind = "contradiction:subject"
                edge_id = _graph_edge_id(ordered[0], ordered[1], edge_kind)
                edge_rows[edge_id] = GraphEdgeRow(
                    pack=pack_name,
                    edge_id=edge_id,
                    source_object_id=ordered[0],
                    target_object_id=ordered[1],
                    edge_kind=edge_kind,
                    weight=0.8,
                    evidence_source_slug="",
                )
                adjacency[ordered[0]].add(ordered[1])
                adjacency[ordered[1]].add(ordered[0])

    cluster_rows: list[GraphClusterRow] = []
    communities = _detect_communities(edge_rows, sorted(object_titles))
    for members in communities:
        center_object_id = max(
            members,
            key=lambda candidate: (
                len(adjacency.get(candidate, set())),
                object_titles.get(candidate, ""),
                candidate,
            ),
        )
        cluster_rows.append(
            GraphClusterRow(
                pack=pack_name,
                cluster_id=_graph_cluster_id(members),
                cluster_kind="louvain_community",
                label=object_titles.get(center_object_id, center_object_id),
                center_object_id=center_object_id,
                member_object_ids_json=json.dumps(members, ensure_ascii=False),
                score=float(len(members)),
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

    objects: list[ObjectRow] = []
    claims: list[ClaimRow] = []
    claim_evidence: list[ClaimEvidenceRow] = []
    compiled_summaries: list[CompiledSummaryRow] = []

    from ...object_kinds import CORE_OBJECT_KINDS, normalize_kind

    from ...object_kinds import KIND_CONCEPT

    for slug, title, note_type, path, _day_id, _frontmatter_json, body in page_rows:
        resolved_kind = note_type if note_type in CORE_OBJECT_KINDS else KIND_CONCEPT
        if _frontmatter_json:
            try:
                fm = json.loads(_frontmatter_json)
                if isinstance(fm, dict):
                    et = fm.get("entity_type", "")
                    if isinstance(et, str) and et:
                        normalized = normalize_kind(et)
                        if normalized in CORE_OBJECT_KINDS:
                            resolved_kind = normalized
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        objects.append(
            ObjectRow(
                pack=resolved_pack_name,
                object_id=slug,
                object_kind=resolved_kind,
                title=title,
                canonical_path=path,
                source_slug=slug,
            )
        )
        summary = _page_summary(body, fallback=title)
        claim_id = _claim_id(slug, summary)
        claims.append(
            ClaimRow(
                pack=resolved_pack_name,
                claim_id=claim_id,
                object_id=slug,
                claim_kind="page_summary",
                claim_text=summary,
                confidence=1.0,
            )
        )
        claim_evidence.append(
            ClaimEvidenceRow(
                pack=resolved_pack_name,
                claim_id=claim_id,
                source_slug=slug,
                evidence_kind="page_summary",
                quote_text=summary,
            )
        )
        compiled_summaries.append(
            CompiledSummaryRow(
                pack=resolved_pack_name,
                object_id=slug,
                summary_text=summary,
                source_slug=slug,
            )
        )

    relations = [
        RelationRow(
            pack=resolved_pack_name,
            source_object_id=source_slug,
            target_object_id=target_slug,
            relation_type=relation_type,
            evidence_source_slug=source_slug,
        )
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
