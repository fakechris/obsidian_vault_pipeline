from __future__ import annotations

import sqlite3
from itertools import islice
from pathlib import Path
from typing import Any

from .discovery import discover_related
from .extraction.artifacts import load_run_results
from .knowledge_index import knowledge_index_stats, recent_audit_events
from .packs.base import BaseDomainPack
from .packs.loader import load_pack
from .runtime import VaultLayout, resolve_vault_dir


def _resolve_pack(pack: str | BaseDomainPack | None) -> BaseDomainPack:
    if isinstance(pack, BaseDomainPack):
        return pack
    return load_pack(pack or "default-knowledge")


def _slug_object_kinds(vault_dir: Path, registry: Any | None = None) -> dict[str, str]:
    if registry is None:
        from .concept_registry import ConceptRegistry

        registry = ConceptRegistry(vault_dir).load()
    return {entry.slug: entry.kind for entry in registry.entries}


def _build_identity_evidence(
    vault_dir: Path,
    mentions: list[str],
    registry: Any | None = None,
    *,
    pack: BaseDomainPack,
) -> list[dict[str, object]]:
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
                "pack": pack.name,
                "object_kind": getattr(registry.find_by_slug(result.entry.slug), "kind", "document") if result.entry else "",
                "ambiguous_slugs": [entry.slug for entry in result.ambiguous_entries],
            }
        )
    return evidence


def _build_retrieval_evidence(
    vault_dir: Path,
    query: str | None,
    mentions: list[str],
    limit: int,
    *,
    pack: BaseDomainPack,
) -> list[dict[str, object]]:
    retrieval_queries = []
    if query:
        retrieval_queries.append(query)
    retrieval_queries.extend(mention for mention in mentions if mention and mention not in retrieval_queries)

    results: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item_query in retrieval_queries[:3]:
        for row in discover_related(vault_dir, item_query, engine="knowledge", limit=limit, pack=pack):
            normalized = {
                "channel": "retrieval",
                "query": item_query,
                "engine": row.get("engine", "knowledge"),
                "kind": row.get("kind", "semantic"),
                "slug": row.get("slug", ""),
                "title": row.get("title", ""),
                "pack": row.get("pack", pack.name),
                "object_kind": row.get("object_kind", "document"),
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


def _build_graph_evidence(
    vault_dir: Path,
    slugs: list[str],
    limit: int,
    *,
    pack: BaseDomainPack,
    slug_kinds: dict[str, str],
) -> list[dict[str, object]]:
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
            "pack": pack.name,
            "source_slug": source_slug,
            "target_slug": target_slug,
            "source_kind": slug_kinds.get(source_slug, "document"),
            "target_kind": slug_kinds.get(target_slug, "document"),
            "link_type": link_type,
        }
        for source_slug, target_slug, link_type in rows
    ]


def _build_audit_evidence(
    vault_dir: Path,
    slugs: list[str],
    limit: int,
    *,
    pack: BaseDomainPack,
    slug_kinds: dict[str, str],
) -> list[dict[str, object]]:
    rows = recent_audit_events(vault_dir, limit=max(limit * 5, 10))
    if slugs:
        rows = [row for row in rows if row.get("slug") in slugs]
    return [
        {
            "channel": "audit",
            "pack": pack.name,
            "source_log": row.get("source_log", ""),
            "event_type": row.get("event_type", ""),
            "slug": row.get("slug", ""),
            "object_kind": slug_kinds.get(str(row.get("slug") or ""), "document"),
            "timestamp": row.get("timestamp", ""),
        }
        for row in rows[:limit]
    ]


def _build_extraction_evidence(
    vault_dir: Path,
    limit: int,
    *,
    pack: BaseDomainPack,
    extraction_profile: str | None,
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for result in load_run_results(VaultLayout.from_vault(vault_dir), pack_name=pack.name, profile_name=extraction_profile):
        for record in result.records:
            span = record.spans[0] if record.spans else None
            evidence.append(
                {
                    "channel": "extraction",
                    "pack": pack.name,
                    "profile": result.profile_name,
                    "object_kind": "document",
                    "source_path": result.source_path,
                    "quote": span.quote if span else "",
                    "char_start": span.char_start if span else 0,
                    "char_end": span.char_end if span else 0,
                    "section_title": span.section_title if span else "",
                    "values": record.values,
                }
            )
    return list(islice(evidence, limit))


def build_evidence_payload(
    vault_dir: Path,
    *,
    query: str | None = None,
    mentions: list[str] | None = None,
    slugs: list[str] | None = None,
    limit: int = 5,
    registry: Any | None = None,
    pack: str | BaseDomainPack | None = None,
    include_extraction: bool = False,
    extraction_profile: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    resolved_pack = _resolve_pack(pack)
    mentions = [mention for mention in (mentions or []) if mention]
    identity_evidence = _build_identity_evidence(
        resolved_vault,
        mentions or ([query] if query else []),
        registry=registry,
        pack=resolved_pack,
    )
    retrieval_evidence = _build_retrieval_evidence(
        resolved_vault,
        query,
        mentions,
        limit=limit,
        pack=resolved_pack,
    )

    derived_slugs = [str(row.get("slug") or "") for row in retrieval_evidence if row.get("slug")]
    graph_targets = list(dict.fromkeys([*(slugs or []), *derived_slugs]))
    slug_kinds = _slug_object_kinds(resolved_vault, registry=registry)

    payload = {
        "identity_evidence": identity_evidence,
        "retrieval_evidence": retrieval_evidence,
        "graph_evidence": _build_graph_evidence(
            resolved_vault,
            graph_targets,
            limit=limit,
            pack=resolved_pack,
            slug_kinds=slug_kinds,
        ),
        "audit_evidence": _build_audit_evidence(
            resolved_vault,
            graph_targets,
            limit=limit,
            pack=resolved_pack,
            slug_kinds=slug_kinds,
        ),
    }
    if include_extraction:
        payload["extraction_evidence"] = _build_extraction_evidence(
            resolved_vault,
            limit=limit,
            pack=resolved_pack,
            extraction_profile=extraction_profile,
        )
    return payload
