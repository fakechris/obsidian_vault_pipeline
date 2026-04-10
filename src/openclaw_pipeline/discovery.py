from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from .extraction.artifacts import load_run_results
from .packs.base import BaseDomainPack
from .packs.loader import load_pack
from .runtime import VaultLayout, resolve_vault_dir


def _snippet_from_page(page: dict[str, object] | None, fallback: str = "") -> str:
    if page is None:
        return fallback
    body = str(page.get("body") or "").strip()
    if not body:
        return fallback
    normalized = " ".join(body.split())
    return normalized[:180]


def _safe_search_knowledge(vault_dir: Path, query: str, limit: int) -> list[dict[str, object]]:
    from .knowledge_index import search_knowledge_index

    try:
        return search_knowledge_index(vault_dir, query, limit=limit)
    except sqlite3.OperationalError:
        normalized_terms = re.findall(r"[A-Za-z0-9]+", query)
        if not normalized_terms:
            return []
        safe_query = " ".join(f'"{term}"' for term in normalized_terms)
        return search_knowledge_index(vault_dir, safe_query, limit=limit)


def _discover_with_knowledge(vault_dir: Path, query: str, limit: int) -> list[dict[str, object]]:
    from .knowledge_index import get_knowledge_page, query_knowledge_index

    lexical_rows = [row for row in _safe_search_knowledge(vault_dir, query, limit=limit) if float(row.get("score", 0.0)) > 0.0]

    results: list[dict[str, object]] = []
    seen_slugs: set[str] = set()

    for row in lexical_rows:
        slug = str(row["slug"])
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        page = get_knowledge_page(vault_dir, slug)
        entry = {
            "engine": "knowledge",
            "kind": "lexical",
            "slug": slug,
            "title": str(row["title"]),
            "score": float(row["score"]),
            "snippet": _snippet_from_page(page),
            "path": str(page["path"]) if page else "",
        }
        results.append(entry)

    if len(results) >= limit:
        return results[:limit]

    semantic_rows = query_knowledge_index(vault_dir, query, limit=limit)
    for row in semantic_rows:
        slug = str(row["slug"])
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        page = get_knowledge_page(vault_dir, slug)
        title = str(page["title"]) if page else slug
        snippet = str(row.get("chunk_text") or "")[:180]
        results.append(
            {
                "engine": "knowledge",
                "kind": "semantic",
                "slug": slug,
                "title": title,
                "score": float(row["score"]),
                "snippet": snippet,
                "path": str(page["path"]) if page else "",
                "section_title": str(row.get("section_title") or ""),
            }
        )
        if len(results) >= limit:
            break

    return results[:limit]


def _discover_with_qmd(vault_dir: Path, query: str, limit: int) -> list[dict[str, object]]:  # noqa: ARG001
    try:
        result = subprocess.run(
            ["qmd", "search", query, "--limit", str(limit)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError("QMD engine requested but qmd is not available") from exc

    if result.returncode != 0:
        raise RuntimeError("QMD engine requested but qmd is not available")

    rows: list[dict[str, object]] = []
    for line in result.stdout.strip().splitlines():
        if "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            continue
        file_path, score_text, title = parts[:3]
        slug = Path(file_path).stem
        try:
            score = float(score_text)
        except ValueError:
            score = 0.0
        rows.append(
            {
                "engine": "qmd",
                "kind": "semantic",
                "slug": slug,
                "title": title,
                "score": score,
                "snippet": "",
                "path": file_path,
            }
        )
    return rows[:limit]


def _discover_with_extraction(
    vault_dir: Path,
    query: str,
    limit: int,
    *,
    pack: BaseDomainPack,
    extraction_profile: str | None,
) -> list[dict[str, object]]:
    normalized_query = query.lower().strip()
    rows: list[dict[str, object]] = []
    layout = VaultLayout.from_vault(vault_dir)
    for result in load_run_results(layout, pack_name=pack.name, profile_name=extraction_profile):
        for record in result.records:
            search_blob = " ".join(str(value) for value in record.values.values()).lower()
            if normalized_query and normalized_query not in search_blob:
                continue
            title = str(
                record.values.get("section_title")
                or record.values.get("claim")
                or record.values.get("subject")
                or record.values.get("step_name")
                or result.profile_name
            )
            rows.append(
                {
                    "engine": "extraction",
                    "kind": "derived",
                    "slug": "",
                    "title": title,
                    "score": 1.0,
                    "snippet": " ".join(str(value) for value in record.values.values())[:180],
                    "path": result.source_path,
                    "profile": result.profile_name,
                }
            )
            if len(rows) >= limit:
                return rows
    return rows


def _resolve_pack(pack: str | BaseDomainPack | None) -> BaseDomainPack:
    if isinstance(pack, BaseDomainPack):
        return pack
    return load_pack(pack or "default-knowledge")


def _slug_object_kinds(vault_dir: Path) -> dict[str, str]:
    from .concept_registry import ConceptRegistry

    registry = ConceptRegistry(vault_dir).load()
    return {entry.slug: entry.kind for entry in registry.entries}


def _annotate_discovery_rows(
    vault_dir: Path,
    rows: list[dict[str, object]],
    pack: BaseDomainPack,
) -> list[dict[str, object]]:
    slug_kinds = _slug_object_kinds(vault_dir)
    allowed_kinds = set(pack.discoverable_object_kinds())

    annotated: list[dict[str, object]] = []
    for row in rows:
        slug = str(row.get("slug") or "")
        object_kind = slug_kinds.get(slug, "document")
        if allowed_kinds and object_kind not in allowed_kinds:
            continue
        normalized = dict(row)
        normalized["pack"] = pack.name
        normalized["object_kind"] = object_kind
        annotated.append(normalized)
    return annotated


def discover_related(
    vault_dir: Path,
    query: str,
    *,
    engine: str = "knowledge",
    limit: int = 10,
    pack: str | BaseDomainPack | None = None,
    include_extraction: bool = False,
    extraction_profile: str | None = None,
) -> list[dict[str, object]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    resolved_pack = _resolve_pack(pack)
    if engine == "knowledge":
        rows = _discover_with_knowledge(resolved_vault, query, limit)
        if include_extraction and len(rows) < limit:
            rows.extend(
                _discover_with_extraction(
                    resolved_vault,
                    query,
                    limit - len(rows),
                    pack=resolved_pack,
                    extraction_profile=extraction_profile,
                )
            )
        return _annotate_discovery_rows(resolved_vault, rows, resolved_pack)
    if engine == "qmd":
        return _annotate_discovery_rows(
            resolved_vault,
            _discover_with_qmd(resolved_vault, query, limit),
            resolved_pack,
        )
    raise ValueError(f"Unsupported discovery engine: {engine}")


def discover_identity_context(
    registry: object,
    mention: str,
    *,
    pack: str | BaseDomainPack | None = None,
) -> dict[str, object]:
    resolved_pack = _resolve_pack(pack)
    resolution = registry.resolve_mention(mention)
    return {
        "action": resolution.action.value if hasattr(resolution.action, "value") else str(resolution.action),
        "mention": resolution.mention,
        "normalized_mention": resolution.normalized_mention,
        "entry_slug": resolution.entry.slug if resolution.entry else "",
        "pack": resolved_pack.name,
        "object_kind": getattr(registry.find_by_slug(resolution.entry.slug), "kind", "document") if resolution.entry else "",
        "confidence": resolution.confidence,
        "ambiguous_slugs": [entry.slug for entry in resolution.ambiguous_entries],
    }


def discover_query_context(
    vault_dir: Path,
    query: str,
    *,
    limit: int = 10,
    pack: str | BaseDomainPack | None = None,
) -> list[dict[str, object]]:
    return discover_related(vault_dir, query, engine="knowledge", limit=limit, pack=pack)
