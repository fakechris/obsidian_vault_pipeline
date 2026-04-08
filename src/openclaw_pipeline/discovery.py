from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from .runtime import resolve_vault_dir


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
    semantic_rows = query_knowledge_index(vault_dir, query, limit=limit)

    results: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for row in lexical_rows:
        slug = str(row["slug"])
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
        key = (entry["kind"], slug)
        if key not in seen:
            seen.add(key)
            results.append(entry)

    for row in semantic_rows:
        slug = str(row["slug"])
        page = get_knowledge_page(vault_dir, slug)
        title = str(page["title"]) if page else slug
        snippet = str(row.get("chunk_text") or "")[:180]
        entry = {
            "engine": "knowledge",
            "kind": "semantic",
            "slug": slug,
            "title": title,
            "score": float(row["score"]),
            "snippet": snippet,
            "path": str(page["path"]) if page else "",
            "section_title": str(row.get("section_title") or ""),
        }
        key = (entry["kind"], slug)
        if key not in seen:
            seen.add(key)
            results.append(entry)

    results.sort(key=lambda item: (item["kind"] != "lexical", -float(item["score"])))
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


def discover_related(
    vault_dir: Path,
    query: str,
    *,
    engine: str = "knowledge",
    limit: int = 10,
) -> list[dict[str, object]]:
    resolved_vault = resolve_vault_dir(vault_dir)
    if engine == "knowledge":
        return _discover_with_knowledge(resolved_vault, query, limit)
    if engine == "qmd":
        return _discover_with_qmd(resolved_vault, query, limit)
    raise ValueError(f"Unsupported discovery engine: {engine}")


def discover_identity_context(registry: object, mention: str) -> dict[str, object]:
    resolution = registry.resolve_mention(mention)
    return {
        "action": resolution.action.value if hasattr(resolution.action, "value") else str(resolution.action),
        "mention": resolution.mention,
        "normalized_mention": resolution.normalized_mention,
        "entry_slug": resolution.entry.slug if resolution.entry else "",
        "confidence": resolution.confidence,
        "ambiguous_slugs": [entry.slug for entry in resolution.ambiguous_entries],
    }


def discover_query_context(vault_dir: Path, query: str, *, limit: int = 10) -> list[dict[str, object]]:
    return discover_related(vault_dir, query, engine="knowledge", limit=limit)
