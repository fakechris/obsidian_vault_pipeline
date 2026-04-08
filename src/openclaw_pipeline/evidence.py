from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .discovery import discover_related
from .knowledge_index import knowledge_index_stats, recent_audit_events
from .runtime import VaultLayout, resolve_vault_dir


def _build_identity_evidence(vault_dir: Path, mentions: list[str], registry: Any | None = None) -> list[dict[str, object]]:
    if not mentions:
        return []
    if registry is None:
        from .concept_registry import ConceptRegistry

        registry = ConceptRegistry(vault_dir).load()

    evidence = []
    for mention in mentions:
        result = registry.resolve_mention(mention)
        evidence.append(
            {
                "channel": "identity",
                "mention": mention,
                "action": result.action.value if hasattr(result.action, "value") else str(result.action),
                "confidence": result.confidence,
                "entry_slug": result.entry.slug if result.entry else "",
                "ambiguous_slugs": [entry.slug for entry in result.ambiguous_entries],
            }
        )
    return evidence


def _build_retrieval_evidence(vault_dir: Path, query: str | None, mentions: list[str], limit: int) -> list[dict[str, object]]:
    retrieval_queries = []
    if query:
        retrieval_queries.append(query)
    retrieval_queries.extend(mention for mention in mentions if mention and mention not in retrieval_queries)

    results: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item_query in retrieval_queries[:3]:
        for row in discover_related(vault_dir, item_query, engine="knowledge", limit=limit):
            normalized = {
                "channel": "retrieval",
                "query": item_query,
                "engine": row.get("engine", "knowledge"),
                "kind": row.get("kind", "semantic"),
                "slug": row.get("slug", ""),
                "title": row.get("title", ""),
                "score": float(row.get("score") or 0.0),
                "snippet": row.get("snippet", ""),
                "path": row.get("path", ""),
            }
            key = (str(normalized["query"]), str(normalized["kind"]), str(normalized["slug"]))
            if key in seen:
                continue
            seen.add(key)
            results.append(normalized)
    return results[:limit]


def _build_graph_evidence(vault_dir: Path, slugs: list[str], limit: int) -> list[dict[str, object]]:
    if not slugs:
        return []

    knowledge_index_stats(vault_dir)
    layout = VaultLayout.from_vault(vault_dir)
    placeholders = ",".join("?" for _ in slugs)
    query = f"""
        SELECT source_slug, target_slug, link_type
        FROM page_links
        WHERE source_slug IN ({placeholders}) OR target_slug IN ({placeholders})
        LIMIT ?
    """
    with sqlite3.connect(layout.knowledge_db) as conn:
        rows = conn.execute(query, (*slugs, *slugs, limit)).fetchall()

    return [
        {
            "channel": "graph",
            "source_slug": source_slug,
            "target_slug": target_slug,
            "link_type": link_type,
        }
        for source_slug, target_slug, link_type in rows
    ]


def _build_audit_evidence(vault_dir: Path, slugs: list[str], limit: int) -> list[dict[str, object]]:
    rows = recent_audit_events(vault_dir, limit=max(limit * 5, 10))
    if slugs:
        rows = [row for row in rows if row.get("slug") in slugs]
    return [
        {
            "channel": "audit",
            "source_log": row.get("source_log", ""),
            "event_type": row.get("event_type", ""),
            "slug": row.get("slug", ""),
            "timestamp": row.get("timestamp", ""),
        }
        for row in rows[:limit]
    ]


def build_evidence_payload(
    vault_dir: Path,
    *,
    query: str | None = None,
    mentions: list[str] | None = None,
    slugs: list[str] | None = None,
    limit: int = 5,
    registry: Any | None = None,
) -> dict[str, list[dict[str, object]]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    mentions = [mention for mention in (mentions or []) if mention]
    identity_evidence = _build_identity_evidence(resolved_vault, mentions or ([query] if query else []), registry=registry)
    retrieval_evidence = _build_retrieval_evidence(resolved_vault, query, mentions, limit=limit)

    derived_slugs = [str(row.get("slug") or "") for row in retrieval_evidence if row.get("slug")]
    graph_targets = list(dict.fromkeys([*(slugs or []), *derived_slugs]))

    return {
        "identity_evidence": identity_evidence,
        "retrieval_evidence": retrieval_evidence,
        "graph_evidence": _build_graph_evidence(resolved_vault, graph_targets, limit=limit),
        "audit_evidence": _build_audit_evidence(resolved_vault, graph_targets, limit=limit),
    }
